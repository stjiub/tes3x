#ifndef MGR_JSON_H
#define MGR_JSON_H

#include <stddef.h>

enum { JSON_NULL, JSON_BOOL, JSON_NUMBER, JSON_STRING, JSON_ARRAY, JSON_OBJECT };

/* One value; for an object, its children alternate key and value. `next` is the token after the
 * whole value, so siblings are reached without walking children. */
struct jtok {
    int type, start, end, next, count;
};

struct json {
    const char *s;
    struct jtok *t;
    int n;
};

int json_parse(struct json *j, const char *s, size_t n);
void json_free(struct json *j);
/* The value under `key` in object `obj`, or -1. */
int json_get(const struct json *j, int obj, const char *key);
/* Children of an array or object: for (i = json_child(j, a); i >= 0; i = json_sibling(j, a, i)) */
int json_child(const struct json *j, int parent);
int json_sibling(const struct json *j, int parent, int i);
/* A string token unescaped into buf (Latin-1, '?' past it); the length, or -1 if not a string. */
int json_string(const struct json *j, int i, char *buf, size_t n);
int json_equals(const struct json *j, int i, const char *s);
long long json_number(const struct json *j, int i);

#endif
