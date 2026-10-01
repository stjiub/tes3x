// Serves decompilation and references for tes3x_sym.py over a loopback socket, one JSON
// request per line. Run by tes3x_ghidra.py; arguments: port, idle seconds, then tag:program.
//@category TES3X
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

import com.google.gson.*;

import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.ApplyFunctionSignatureCmd;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.*;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.NamespaceUtils;
import ghidra.app.util.parser.FunctionSignatureParser;
import ghidra.feature.vt.api.correlator.program.*;
import ghidra.feature.vt.api.db.VTSessionDB;
import ghidra.feature.vt.api.main.*;
import ghidra.feature.vt.api.util.VTOptions;
import ghidra.framework.model.DomainFile;
import ghidra.program.model.address.*;
import ghidra.program.model.data.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;

public class Tes3xServe extends GhidraScript {
	private final Map<String, Program> programs = new HashMap<>();
	private final Map<String, DecompInterface> decomps = new HashMap<>();
	private final Map<String, String> stamps = new HashMap<>();
	private final Gson gson = new Gson();

	@Override
	public void run() throws Exception {
		String[] args = getScriptArgs();
		int port = Integer.parseInt(args[0]);
		int idle = Integer.parseInt(args[1]);
		for (int i = 2; i < args.length; i++) {
			String[] kv = args[i].split(":", 2);
			Program p = kv[1].equals(currentProgram.getName()) ? currentProgram : open(kv[1]);
			programs.put(kv[0], p);
			DecompInterface d = new DecompInterface();
			d.setOptions(new DecompileOptions());
			d.toggleCCode(true);
			d.setSimplificationStyle("decompile");
			d.openProgram(p);
			decomps.put(kv[0], d);
		}
		try (ServerSocket ss = new ServerSocket(port, 16, InetAddress.getLoopbackAddress())) {
			ss.setSoTimeout(idle * 1000);
			println("tes3x-serve ready on " + port);
			while (true) {
				Socket s;
				try {
					s = ss.accept();
				}
				catch (SocketTimeoutException e) {
					break;
				}
				if (!serve(s)) {
					break;
				}
			}
		}
		for (DecompInterface d : decomps.values()) {
			d.dispose();
		}
		for (Program p : programs.values()) {
			if (p != currentProgram) {
				p.release(this);
			}
		}
	}

	private Program open(String name) throws Exception {
		DomainFile f = state.getProject().getProjectData().getRootFolder().getFile(name);
		if (f == null) {
			throw new IllegalArgumentException("no program " + name + " in the project");
		}
		return (Program) f.getDomainObject(this, false, false, monitor);
	}

	private boolean serve(Socket s) throws IOException {
		boolean keep = true;
		try (s;
				BufferedReader in = new BufferedReader(
					new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
				Writer out = new OutputStreamWriter(s.getOutputStream(), StandardCharsets.UTF_8)) {
			String line;
			while ((line = in.readLine()) != null) {
				JsonObject req = JsonParser.parseString(line).getAsJsonObject();
				JsonObject res;
				try {
					String op = req.get("op").getAsString();
					if (op.equals("stop")) {
						keep = false;
						res = new JsonObject();
					}
					else {
						res = handle(op, req);
					}
					res.addProperty("ok", true);
				}
				catch (Exception e) {
					res = new JsonObject();
					res.addProperty("ok", false);
					res.addProperty("error", e.toString());
				}
				out.write(gson.toJson(res) + "\n");
				out.flush();
				if (!keep) {
					break;
				}
			}
		}
		return keep;
	}

	private JsonObject handle(String op, JsonObject req) throws Exception {
		JsonObject res = new JsonObject();
		if (op.equals("ping")) {
			res.add("stamps", gson.toJsonTree(stamps));
			return res;
		}
		String tag = req.get("tag").getAsString();
		Program p = programs.get(tag);
		if (p == null) {
			throw new IllegalArgumentException("no program for tag " + tag);
		}
		switch (op) {
			case "sync":
				res = sync(p, req);
				stamps.put(tag, req.get("stamp").getAsString());
				return res;
			case "decompile":
				return decompile(tag, p, addr(p, req.get("va").getAsString()),
					req.has("timeout") ? req.get("timeout").getAsInt() : 60);
			case "refs":
				return refs(p, addr(p, req.get("va").getAsString()));
			case "correlate":
				return correlate(req);
			default:
				throw new IllegalArgumentException("unknown op " + op);
		}
	}

	private Address addr(Program p, String va) {
		return p.getAddressFactory().getDefaultAddressSpace().getAddress(Long.decode(va));
	}

	// Types first, so that names can find their class struct and signatures their types.
	private JsonObject sync(Program p, JsonObject req) {
		JsonObject res = new JsonObject();
		List<String> errors = new ArrayList<>();
		int tx = p.startTransaction("tes3x sync");
		try {
			if (req.has("types")) {
				res.addProperty("types", types(p, req.getAsJsonObject("types"), errors));
			}
			res.addProperty("records", records(p, req.getAsJsonArray("records"), errors));
		}
		finally {
			p.endTransaction(tx, true);
		}
		res.add("errors", gson.toJsonTree(errors));
		return res;
	}

	private static CategoryPath category(String path) {
		int i = path.lastIndexOf('/');
		return i <= 0 ? CategoryPath.ROOT : new CategoryPath(path.substring(0, i));
	}

	private static String leaf(String path) {
		return path.substring(path.lastIndexOf('/') + 1);
	}

	private DataType primitive(String name) {
		switch (name) {
			case "bool": return BooleanDataType.dataType;
			case "char": return CharDataType.dataType;
			case "uchar": return ByteDataType.dataType;
			case "short": return ShortDataType.dataType;
			case "ushort": return UnsignedShortDataType.dataType;
			case "int": return IntegerDataType.dataType;
			case "uint": return UnsignedIntegerDataType.dataType;
			case "longlong": return LongLongDataType.dataType;
			case "ulonglong": return UnsignedLongLongDataType.dataType;
			case "float": return FloatDataType.dataType;
			case "double": return DoubleDataType.dataType;
			case "wchar16": return WideChar16DataType.dataType;
			case "void": return VoidDataType.dataType;
		}
		if (name.startsWith("undefined")) {
			return Undefined.getUndefinedDataType(Integer.parseInt(name.substring(9)));
		}
		return null;
	}

	// "/TES3/Statistic*[8]": a base type, then pointer and array suffixes applied in order.
	private DataType resolve(DataTypeManager dtm, String t) {
		int i = t.length();
		while (i > 0 && (t.charAt(i - 1) == '*' || t.charAt(i - 1) == ']')) {
			i = t.charAt(i - 1) == '*' ? i - 1 : t.lastIndexOf('[', i - 1);
		}
		String base = t.substring(0, i);
		DataType dt = primitive(base);
		if (dt == null) {
			dt = dtm.getDataType(category(base), leaf(base));
		}
		if (dt == null) {
			dt = Undefined.getUndefinedDataType(1);
			if (i == t.length()) {
				return null;
			}
		}
		while (i < t.length()) {
			if (t.charAt(i) == '*') {
				dt = new PointerDataType(dt, 4, dtm);
				i++;
			}
			else {
				int end = t.indexOf(']', i);
				int n = Integer.parseInt(t.substring(i + 1, end));
				if (n <= 0 || dt.getLength() <= 0) {
					return null;
				}
				dt = new ArrayDataType(dt, n, dt.getLength(), dtm);
				i = end + 1;
			}
		}
		return dt;
	}

	private int types(Program p, JsonObject types, List<String> errors) {
		DataTypeManager dtm = p.getDataTypeManager();
		DataTypeConflictHandler replace = DataTypeConflictHandler.REPLACE_HANDLER;
		for (Map.Entry<String, JsonElement> e : types.getAsJsonObject("enums").entrySet()) {
			JsonObject o = e.getValue().getAsJsonObject();
			EnumDataType en = new EnumDataType(category(e.getKey()), leaf(e.getKey()),
				o.get("size").getAsInt(), dtm);
			for (Map.Entry<String, JsonElement> v : o.getAsJsonObject("values").entrySet()) {
				try {
					en.add(v.getKey(), v.getValue().getAsLong());
				}
				catch (IllegalArgumentException ex) {
					// Duplicate names or values out of range: keep the rest.
				}
			}
			dtm.addDataType(en, replace);
		}
		JsonObject records = types.getAsJsonObject("records");
		Map<String, Composite> made = new HashMap<>();
		for (Map.Entry<String, JsonElement> e : records.entrySet()) {
			JsonObject o = e.getValue().getAsJsonObject();
			CategoryPath cp = category(e.getKey());
			Composite c = o.get("kind").getAsString().equals("union")
					? new UnionDataType(cp, leaf(e.getKey()), dtm)
					: new StructureDataType(cp, leaf(e.getKey()), o.get("size").getAsInt(), dtm);
			made.put(e.getKey(), (Composite) dtm.addDataType(c, replace));
		}
		// Unions take their size from their members, so fill them before anything embeds them.
		List<String> order = new ArrayList<>(records.keySet());
		order.sort(Comparator.comparing(k -> !(made.get(k) instanceof Union)));
		for (String key : order) {
			JsonObject o = records.getAsJsonObject(key);
			Composite c = made.get(key);
			if (o.has("bases")) {
				for (JsonElement b : o.getAsJsonArray("bases")) {
					JsonArray a = b.getAsJsonArray();
					DataType dt = made.get(a.get(1).getAsString());
					if (dt != null && dt.getLength() > 0 && c instanceof Structure s) {
						place(s, a.get(0).getAsInt(), dt, "base_" + dt.getName(), key, errors);
					}
				}
			}
			for (JsonElement f : o.getAsJsonArray("fields")) {
				JsonArray a = f.getAsJsonArray();
				DataType dt = resolve(dtm, a.get(2).getAsString());
				if (dt == null || dt.getLength() <= 0) {
					continue;
				}
				String name = a.get(1).getAsString();
				if (c instanceof Structure s) {
					place(s, a.get(0).getAsInt(), dt, name, key, errors);
				}
				else {
					c.add(dt, dt.getLength(), name, null);
				}
			}
			if (o.has("note")) {
				c.setDescription(o.get("note").getAsString());
			}
		}
		return made.size();
	}

	private void place(Structure s, int off, DataType dt, String name, String key,
			List<String> errors) {
		try {
			if (off + dt.getLength() > s.getLength()) {
				throw new IllegalArgumentException("past the end");
			}
			s.replaceAtOffset(off, dt, dt.getLength(), name, null);
		}
		catch (IllegalArgumentException ex) {
			errors.add(key + "+0x" + Integer.toHexString(off) + " " + name + ": " +
				ex.getMessage());
		}
	}

	// The class a "Class::method" name belongs to, qualified by the namespace its struct is in.
	private String classPath(DataTypeManager dtm, String cls) {
		if (cls.contains("::")) {
			return cls;
		}
		for (String ns : new String[] { "TES3", "NI", "TES3/UI" }) {
			if (dtm.getDataType(new CategoryPath("/" + ns), cls) instanceof Structure) {
				return ns.replace("/", "::") + "::" + cls;
			}
		}
		return null;
	}

	private boolean readsEcxFirst(Program p, Function f) {
		Register ecx = p.getRegister("ECX");
		InstructionIterator it = p.getListing().getInstructions(f.getEntryPoint(), true);
		for (int i = 0; i < 16 && it.hasNext(); i++) {
			Instruction ins = it.next();
			// MSVC reserves a stack slot with push ecx; that is not a use of this.
			if (ins.getMnemonicString().equals("PUSH") && ecx.equals(ins.getRegister(0))) {
				continue;
			}
			for (Object o : ins.getInputObjects()) {
				if (o instanceof Register r && ecx.contains(r)) {
					return true;
				}
			}
			for (Object o : ins.getResultObjects()) {
				if (o instanceof Register r && r.contains(ecx)) {
					return false;
				}
			}
			if (!ins.getFlowType().isFallthrough()) {
				return false;
			}
		}
		return false;
	}

	private int records(Program p, JsonArray records, List<String> errors) {
		int applied = 0;
		DataTypeManager dtm = p.getDataTypeManager();
		SymbolTable st = p.getSymbolTable();
		for (JsonElement el : records) {
			JsonObject r = el.getAsJsonObject();
			Address a = addr(p, r.get("va").getAsString());
			String name = r.get("name").getAsString().replaceAll("\\s+", "_");
			try {
				Function f = p.getFunctionManager().getFunctionAt(a);
				if (f == null) {
					st.createLabel(a, name, SourceType.USER_DEFINED).setPrimary();
					if (r.has("type")) {
						DataType dt = resolve(dtm, r.get("type").getAsString());
						if (dt != null) {
							DataUtilities.createData(p, a, dt, -1,
								DataUtilities.ClearDataMode.CLEAR_ALL_CONFLICT_DATA);
						}
					}
				}
				else {
					function(p, f, name, r, errors);
				}
				if (r.has("note")) {
					p.getListing().setComment(a, CodeUnit.PLATE_COMMENT,
						r.get("note").getAsString());
				}
				applied++;
			}
			catch (Exception ex) {
				errors.add(r.get("va").getAsString() + " " + name + ": " + ex.getMessage());
			}
		}
		return applied;
	}

	private void function(Program p, Function f, String name, JsonObject r, List<String> errors)
			throws Exception {
		int sep = name.lastIndexOf("::");
		String cls = sep > 0 ? classPath(p.getDataTypeManager(), name.substring(0, sep)) : null;
		if (cls != null) {
			Namespace ns = NamespaceUtils.createNamespaceHierarchy(cls, null, p,
				SourceType.USER_DEFINED);
			if (!(ns instanceof GhidraClass)) {
				ns = NamespaceUtils.convertNamespaceToClass(ns);
			}
			f.setParentNamespace(ns);
			f.setName(name.substring(sep + 2), SourceType.USER_DEFINED);
		}
		else {
			f.setName(name, SourceType.USER_DEFINED);
		}
		String conv = r.has("convention") ? r.get("convention").getAsString() : null;
		if (r.has("signature")) {
			FunctionSignatureParser parser = new FunctionSignatureParser(p.getDataTypeManager(),
				null);
			FunctionDefinitionDataType def = parser.parse(f.getSignature(),
				r.get("signature").getAsString());
			if (!new ApplyFunctionSignatureCmd(f.getEntryPoint(), def, SourceType.USER_DEFINED,
				true, false).applyTo(p)) {
				errors.add(r.get("va").getAsString() + " signature not applied");
			}
		}
		if (conv == null && cls != null && readsEcxFirst(p, f)) {
			conv = "__thiscall";
		}
		if (conv != null) {
			f.setCallingConvention(conv);
		}
	}

	private Function function(Program p, Address a) {
		Function f = p.getFunctionManager().getFunctionContaining(a);
		if (f != null) {
			return f;
		}
		int tx = p.startTransaction("tes3x function");
		try {
			if (p.getListing().getInstructionAt(a) == null) {
				new DisassembleCommand(a, null, true).applyTo(p, monitor);
			}
			new CreateFunctionCmd(a).applyTo(p, monitor);
		}
		finally {
			p.endTransaction(tx, true);
		}
		return p.getFunctionManager().getFunctionContaining(a);
	}

	private JsonObject decompile(String tag, Program p, Address a, int timeout) {
		Function f = function(p, a);
		if (f == null) {
			throw new IllegalArgumentException("no function at " + a);
		}
		DecompileResults r = decomps.get(tag).decompileFunction(f, timeout, monitor);
		if (!r.decompileCompleted()) {
			throw new IllegalStateException("decompile failed: " + r.getErrorMessage());
		}
		JsonObject res = new JsonObject();
		res.addProperty("entry", f.getEntryPoint().getOffset());
		res.addProperty("name", f.getName());
		res.addProperty("c", r.getDecompiledFunction().getC());
		return res;
	}

	private JsonObject refs(Program p, Address a) {
		JsonArray list = new JsonArray();
		for (Reference r : p.getReferenceManager().getReferencesTo(a)) {
			JsonObject o = new JsonObject();
			Address from = r.getFromAddress();
			o.addProperty("from", from.getOffset());
			o.addProperty("type", r.getReferenceType().getName());
			Function f = p.getFunctionManager().getFunctionContaining(from);
			if (f != null) {
				o.addProperty("func", f.getEntryPoint().getOffset());
				o.addProperty("name", f.getName());
			}
			Instruction ins = p.getListing().getInstructionAt(from);
			if (ins != null) {
				o.addProperty("insn", ins.toString());
			}
			list.add(o);
		}
		JsonObject res = new JsonObject();
		res.add("refs", list);
		return res;
	}

	private JsonObject correlate(JsonObject req) throws Exception {
		Program source = programs.get("xbe");
		Program destination = programs.get("pc");
		String kind = req.get("kind").getAsString();
		VTProgramCorrelatorFactory factory;
		if (kind.equals("instructions")) {
			factory = new ExactMatchInstructionsProgramCorrelatorFactory();
		}
		else if (kind.equals("mnemonics")) {
			factory = new ExactMatchMnemonicsProgramCorrelatorFactory();
		}
		else if (kind.equals("function-references")) {
			factory = new FunctionReferenceProgramCorrelatorFactory();
		}
		else if (kind.equals("combined-references")) {
			factory = new CombinedFunctionAndDataReferenceProgramCorrelatorFactory();
		}
		else {
			throw new IllegalArgumentException("unknown correlator " + kind);
		}
		AddressSet xs = new AddressSet(
			addr(source, req.get("xbe_lo").getAsString()),
			addr(source, req.get("xbe_hi").getAsString()));
		AddressSet ps = new AddressSet(
			addr(destination, req.get("pc_lo").getAsString()),
			addr(destination, req.get("pc_hi").getAsString()));
		VTSessionDB session = new VTSessionDB("tes3x correlate", source, destination, this);
		JsonArray rows = new JsonArray();
		int tx = session.startTransaction("tes3x correlate");
		try {
			if (req.has("seeds")) {
				VTMatchSet manual = session.getManualMatchSet();
				for (JsonElement el : req.getAsJsonArray("seeds")) {
					JsonArray pair = el.getAsJsonArray();
					Address x = addr(source, pair.get(0).getAsString());
					Address p = addr(destination, pair.get(1).getAsString());
					if (source.getFunctionManager().getFunctionAt(x) == null ||
						destination.getFunctionManager().getFunctionAt(p) == null) {
						continue;
					}
					VTMatchInfo info = new VTMatchInfo(manual);
					info.setAssociationType(VTAssociationType.FUNCTION);
					info.setSourceAddress(x);
					info.setDestinationAddress(p);
					info.setSourceLength(1);
					info.setDestinationLength(1);
					info.setSimilarityScore(new VTScore(1.0));
					info.setConfidenceScore(new VTScore(10.0));
					manual.addMatch(info).getAssociation().setAccepted();
				}
			}
			VTOptions options = factory.createDefaultOptions();
			VTProgramCorrelator correlator = factory.createCorrelator(
				source, xs, destination, ps, options);
			VTMatchSet matches = correlator.correlate(session, monitor);
			for (VTMatch match : matches.getMatches()) {
				JsonObject row = new JsonObject();
				row.addProperty("xbe", match.getSourceAddress().getOffset());
				row.addProperty("pc", match.getDestinationAddress().getOffset());
				row.addProperty("similarity", match.getSimilarityScore().getScore());
				row.addProperty("confidence", match.getConfidenceScore().getScore());
				rows.add(row);
			}
		}
		finally {
			session.endTransaction(tx, true);
			session.release(this);
		}
		JsonObject res = new JsonObject();
		res.add("matches", rows);
		return res;
	}
}
