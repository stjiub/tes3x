// Serves decompilation and references for tes3x_sym.py over a loopback socket, one JSON
// request per line. Run by tes3x_ghidra.py; arguments: port, idle seconds, then tag:program.
//@category TES3X
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

import com.google.gson.*;

import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.*;
import ghidra.app.script.GhidraScript;
import ghidra.framework.model.DomainFile;
import ghidra.program.model.address.*;
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
			case "names":
				res.addProperty("applied", names(p, req.getAsJsonObject("names")));
				stamps.put(tag, req.get("stamp").getAsString());
				return res;
			case "decompile":
				return decompile(tag, p, addr(p, req.get("va").getAsString()),
					req.has("timeout") ? req.get("timeout").getAsInt() : 60);
			case "refs":
				return refs(p, addr(p, req.get("va").getAsString()));
			default:
				throw new IllegalArgumentException("unknown op " + op);
		}
	}

	private Address addr(Program p, String va) {
		return p.getAddressFactory().getDefaultAddressSpace().getAddress(Long.decode(va));
	}

	private int names(Program p, JsonObject names) {
		int applied = 0;
		int tx = p.startTransaction("tes3x names");
		try {
			SymbolTable st = p.getSymbolTable();
			for (Map.Entry<String, JsonElement> e : names.entrySet()) {
				Address a = addr(p, e.getKey());
				String name = e.getValue().getAsString().replaceAll("\\s+", "_");
				try {
					Function f = p.getFunctionManager().getFunctionAt(a);
					if (f != null) {
						f.setName(name, SourceType.USER_DEFINED);
					}
					else {
						st.createLabel(a, name, SourceType.USER_DEFINED).setPrimary();
					}
					applied++;
				}
				catch (Exception ex) {
					// A name Ghidra rejects stays unapplied; the rest still go in.
				}
			}
		}
		finally {
			p.endTransaction(tx, true);
		}
		return applied;
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
}
