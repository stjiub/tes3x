#include "json.h"

#include <stdlib.h>
#include <string.h>

struct parser {
    struct json *j;
    size_t pos, n;
    int cap, depth;
};

static int add(struct parser *p, int type, int start)
{
    struct jtok *t;

    if (p->j->n == p->cap) {
        p->cap = p->cap ? p->cap * 2 : 1024;
        if (!(t = realloc(p->j->t, (size_t)p->cap * sizeof(*t))))
            return -1;
        p->j->t = t;
    }
    t = &p->j->t[p->j->n];
    t->type = type, t->start = start, t->end = start, t->next = 0, t->count = 0;
    return p->j->n++;
}

static void space(struct parser *p)
{
    const char *s = p->j->s;

    while (p->pos < p->n && (s[p->pos] == ' ' || s[p->pos] == '\t' || s[p->pos] == '\r'
                             || s[p->pos] == '\n'))
        p->pos++;
}

static int value(struct parser *p);

static int string(struct parser *p)
{
    const char *s = p->j->s;
    int i = add(p, JSON_STRING, (int)++p->pos);

    if (i < 0)
        return -1;
    while (p->pos < p->n && s[p->pos] != '"') {
        if (s[p->pos] == '\\')
            p->pos++;
        p->pos++;
    }
    if (p->pos >= p->n)
        return -1;
    p->j->t[i].end = (int)p->pos++;
    p->j->t[i].next = p->j->n;
    return i;
}

static int container(struct parser *p, int type, char close)
{
    int i = add(p, type, (int)p->pos++), child;

    if (i < 0 || ++p->depth > 64)
        return -1;
    space(p);
    if (p->pos < p->n && p->j->s[p->pos] == close) {
        p->pos++;
    } else {
        for (;;) {
            if (type == JSON_OBJECT) {
                space(p);
                if (p->pos >= p->n || p->j->s[p->pos] != '"' || string(p) < 0)
                    return -1;
                space(p);
                if (p->pos >= p->n || p->j->s[p->pos++] != ':')
                    return -1;
            }
            if ((child = value(p)) < 0)
                return -1;
            p->j->t[i].count++;
            space(p);
            if (p->pos >= p->n)
                return -1;
            if (p->j->s[p->pos] == ',') {
                p->pos++;
                continue;
            }
            if (p->j->s[p->pos++] != close)
                return -1;
            break;
        }
    }
    p->depth--;
    p->j->t[i].end = (int)p->pos;
    p->j->t[i].next = p->j->n;
    return i;
}

static int value(struct parser *p)
{
    const char *s = p->j->s;
    int i, type;

    space(p);
    if (p->pos >= p->n)
        return -1;
    switch (s[p->pos]) {
    case '{':
        return container(p, JSON_OBJECT, '}');
    case '[':
        return container(p, JSON_ARRAY, ']');
    case '"':
        return string(p);
    }
    type = s[p->pos] == 'n' ? JSON_NULL : s[p->pos] == 't' || s[p->pos] == 'f' ? JSON_BOOL
                                                                                 : JSON_NUMBER;
    if ((i = add(p, type, (int)p->pos)) < 0)
        return -1;
    while (p->pos < p->n && !strchr(",]} \t\r\n", s[p->pos]))
        p->pos++;
    p->j->t[i].end = (int)p->pos;
    p->j->t[i].next = p->j->n;
    return i;
}

int json_parse(struct json *j, const char *s, size_t n)
{
    struct parser p = {j, 0, n, 0, 0};

    j->s = s, j->t = NULL, j->n = 0;
    if (value(&p) < 0) {
        json_free(j);
        return -1;
    }
    return j->n;
}

void json_free(struct json *j)
{
    free(j->t);
    j->t = NULL;
    j->n = 0;
}

int json_child(const struct json *j, int parent)
{
    if (parent < 0 || j->t[parent].count == 0)
        return -1;
    return parent + 1;
}

int json_sibling(const struct json *j, int parent, int i)
{
    int next = j->t[i].next;

    if (j->t[parent].type == JSON_OBJECT)
        next = j->t[next].next;
    return next < j->t[parent].next ? next : -1;
}

int json_get(const struct json *j, int obj, const char *key)
{
    int i;

    if (obj < 0 || j->t[obj].type != JSON_OBJECT)
        return -1;
    for (i = json_child(j, obj); i >= 0; i = json_sibling(j, obj, i))
        if (json_equals(j, i, key))
            return j->t[i].next;
    return -1;
}

static int hex4(const char *s)
{
    int v = 0, i, c;

    for (i = 0; i < 4; i++) {
        c = s[i];
        v = v * 16 + (c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10
                      : c >= 'A' && c <= 'F' ? c - 'A' + 10 : 0);
    }
    return v;
}

int json_string(const struct json *j, int i, char *buf, size_t n)
{
    const char *s;
    int k, end, c;
    size_t o = 0;

    if (i < 0 || j->t[i].type != JSON_STRING || n == 0)
        return -1;
    s = j->s, end = j->t[i].end;
    for (k = j->t[i].start; k < end; k++) {
        c = (unsigned char)s[k];
        if (c == '\\' && k + 1 < end) {
            c = s[++k];
            switch (c) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case 'u':
                c = k + 4 < end ? hex4(s + k + 1) : '?';
                k += 4;
                if (c > 0xFF)
                    c = '?';
                break;
            }
        }
        if (o + 1 < n)
            buf[o++] = (char)c;
    }
    buf[o] = 0;
    return (int)o;
}

int json_equals(const struct json *j, int i, const char *s)
{
    size_t n = strlen(s);

    return i >= 0 && j->t[i].type == JSON_STRING && (size_t)(j->t[i].end - j->t[i].start) == n
           && !memcmp(j->s + j->t[i].start, s, n);
}

long long json_number(const struct json *j, int i)
{
    long long v = 0;
    int k, neg = 0;

    if (i < 0 || j->t[i].type != JSON_NUMBER)
        return 0;
    k = j->t[i].start;
    if (j->s[k] == '-')
        neg = 1, k++;
    for (; k < j->t[i].end && j->s[k] >= '0' && j->s[k] <= '9'; k++)
        v = v * 10 + (j->s[k] - '0');
    return neg ? -v : v;
}
