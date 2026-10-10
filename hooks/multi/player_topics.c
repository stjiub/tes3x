#define PLAYER_TOPICS 17u
#define TOPICS_MAX 4096u
#define TOPIC_NAME_MAX 63u
#define PLAYER_TOPICS_LIST 0x674

static const u8 *topics_sent[TOPICS_MAX];
static u32 topics_count, topics_known, topics_out, topics_in, topics_bad;

static int topic_safe(const u8 *name, u32 length)
{
    u32 i;

    if (!script_safe(name, length))
        return 0;
    for (i = 0; i + 1 < length; i++)
        if (name[i] == 127)
            return 0;
    return 1;
}

static const u8 *topics_head(const u8 *mobile)
{
    const u8 *list = *(const u8 *const *)(mobile + PLAYER_TOPICS_LIST);

    return plausible(list) ? *(const u8 *const *)(list + 8) : 0;
}

/* The native list contains Dialogue pointers, sorted by name. */
static void topics_scan(const u8 *mobile, int send)
{
    const u8 *node = topics_head(mobile), *topic;
    const u8 *pending[EVENT_DATA / 2];
    const char *name;
    u8 data[EVENT_DATA];
    u32 guard, size, i, n = 0, length = 2;

    if (!topics_known) {
        if (send) {
            data[0] = PLAYER_TOPICS;
            data[1] = 0;
            if (!event_queue(EVENT_PLAYER, data, 2))
                return;
        }
        topics_known = 1;
    }
    for (guard = 0; guard < 65536; guard++) {
        topic = plausible(node) ? *(const u8 *const *)(node + 8) : 0;
        if (plausible(topic) && topic[DIALOGUE_TYPE] == 0 &&
            !spell_object_in(topic, topics_sent, topics_count)) {
            name = dialogue_name(topic);
            for (size = 0; name && name[size] && size <= TOPIC_NAME_MAX; size++)
                ;
            if (!name || !size || size > TOPIC_NAME_MAX ||
                !topic_safe((const u8 *)name, size + 1) || topics_count + n == TOPICS_MAX) {
                topics_bad++;
                node = *(const u8 *const *)(node + 4);
                continue;
            }
        } else {
            if (plausible(node)) {
                node = *(const u8 *const *)(node + 4);
                continue;
            }
            topic = 0;
            size = 0;
            name = 0;
        }
        if (n && (!topic || length + size + 1 > EVENT_DATA)) {
            data[0] = PLAYER_TOPICS;
            data[1] = (u8)n;
            if (send && !event_queue(EVENT_PLAYER, data, length))
                return;
            for (i = 0; i < n; i++)
                topics_sent[topics_count++] = pending[i];
            topics_out += send;
            n = 0;
            length = 2;
        }
        if (!topic)
            return;
        copy(data + length, (const u8 *)name, size + 1);
        length += size + 1;
        pending[n++] = topic;
        node = *(const u8 *const *)(node + 4);
    }
    snapshot_deferred++;
}

static void topics_apply(const u8 *data, u32 length)
{
    char line[TOPIC_NAME_MAX + 16];
    u32 off = 2, end, i;

    /* Validate the complete event before running any commands. */
    for (i = 0; i < data[1]; i++) {
        for (end = off; end < length && data[end]; end++)
            ;
        if (end == length || end == off || end - off > TOPIC_NAME_MAX ||
            !topic_safe(data + off, end - off + 1)) {
            topics_bad++;
            return;
        }
        off = end + 1;
    }
    if (off != length) {
        topics_bad++;
        return;
    }
    for (i = 0, off = 2; i < data[1]; i++) {
        char *out = put_text(line, "AddTopic \"");
        out = put_text(out, (const char *)data + off);
        *out++ = '"';
        *out = 0;
        run_script(line);
        while (data[off++])
            ;
        topics_in++;
    }
    tes3x_log("net.player_topics_applied", data[1]);
}

static void topic_query(const char *name)
{
    const u8 *mobile = player_mobile(), *node, *topic;
    const char *known;
    u32 guard;

    if (!plausible(mobile))
        return;
    node = topics_head(mobile);
    for (guard = 0; plausible(node) && guard < 65536; guard++) {
        topic = *(const u8 *const *)(node + 8);
        if (plausible(topic) && (known = dialogue_name(topic)) && same_name(name, known)) {
            tes3x_log("net.topic_known", 1);
            return;
        }
        node = *(const u8 *const *)(node + 4);
    }
    tes3x_log("net.topic_known", 0);
}
