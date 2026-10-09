/* E:\TES3X\console.ini: the [Xbox] settings that belong to this console rather than a build. */

#include "mgr.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#define CONSOLE_DIR "E:\\TES3X"

/* Walks text's lines; for the [Xbox] line setting `key` (NULL for none) gives its start, value and
 * end. *insert, when given, ends up at the end of the section's last line, or NULL if there is no
 * [Xbox] section. */
static int ini_find(const char *text, const char *key, const char **line_at, const char **value,
                    const char **end_at, const char **insert)
{
    const char *line = text, *end, *eq, *v;
    size_t len;
    int in_xbox = 0;

    if (insert)
        *insert = NULL;
    for (; *line; line = *end ? end + 1 : end) {
        end = line + strcspn(line, "\r\n");
        for (v = line; v < end && (*v == ' ' || *v == '\t'); v++)
            ;
        if (*v == '[') {
            in_xbox = end - v >= 6 && !name_cmp_n(v, "[Xbox]", 6);
            if (in_xbox && insert)
                *insert = end;
            continue;
        }
        eq = memchr(v, '=', (size_t)(end - v));
        if (!in_xbox || !eq || *v == ';')
            continue;
        if (insert)
            *insert = end;
        len = (size_t)(eq - v);
        while (len && (v[len - 1] == ' ' || v[len - 1] == '\t'))
            len--;
        if (!key || len != strlen(key) || name_cmp_n(v, key, len))
            continue;
        for (eq++; eq < end && (*eq == ' ' || *eq == '\t'); eq++)
            ;
        *line_at = line, *value = eq, *end_at = end;
        return 1;
    }
    return 0;
}

int ini_get(const char *text, const char *key, char *out, size_t n)
{
    const char *line, *v, *end;
    size_t len;

    if (!ini_find(text, key, &line, &v, &end, NULL))
        return 0;
    len = (size_t)(end - v);
    while (len && (v[len - 1] == ' ' || v[len - 1] == '\t'))
        len--;
    if (len >= n)
        return 0;
    memcpy(out, v, len);
    out[len] = 0;
    return (int)len;
}

int console_get(const char *key, char *out, size_t n)
{
    unsigned char *data;
    size_t size;
    int len;

    if (read_file(CONSOLE_INI, &data, &size))
        return 0;
    len = ini_get((char *)data, key, out, n);
    free(data);
    return len;
}

/* Sets one key and keeps the rest, as tes3x_deploy.merge_console_ini does. */
int console_set(const char *key, const char *value)
{
    unsigned char *data;
    const char *text, *line, *v, *end, *insert;
    char *out;
    size_t n = 0, cap;
    FILE *f;
    int put;

    if (read_file(CONSOLE_INI, &data, &n))
        data = NULL, n = 0;
    text = data ? (const char *)data : "";
    cap = n + strlen(key) + strlen(value) + 16;
    if (!(out = malloc(cap))) {
        free(data);
        return -1;
    }
    if (ini_find(text, key, &line, &v, &end, &insert))
        n = (size_t)snprintf(out, cap, "%.*s%s=%s%s", (int)(line - text), text, key, value, end);
    else if (insert)
        n = (size_t)snprintf(out, cap, "%.*s\r\n%s=%s%s%s", (int)(insert - text), text, key, value,
                             *insert ? "" : "\r\n", insert);
    else
        n = (size_t)snprintf(out, cap, "%s%s[Xbox]\r\n%s=%s\r\n", text,
                             n && text[n - 1] != '\n' ? "\r\n" : "", key, value);
    free(data);
    CreateDirectoryA(CONSOLE_DIR, NULL);
    if (!(f = fopen(CONSOLE_INI ".new", "wb"))) {
        free(out);
        return -1;
    }
    put = fwrite(out, 1, n, f) == n;
    free(out);
    if (fclose(f) || !put) {
        DeleteFileA(CONSOLE_INI ".new");
        return -1;
    }
    DeleteFileA(CONSOLE_INI);
    if (!MoveFileA(CONSOLE_INI ".new", CONSOLE_INI))
        return -1;
    mgr_log("console.ini: %s=%s\n", key, value);
    return 0;
}
