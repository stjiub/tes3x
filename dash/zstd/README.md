zstd 1.5.7's decoder as one file, for rebuilding XBEs from their deltas. Made from the release's
`build/single_file_libs`:

```
python combine.py -r ../../lib -x legacy/zstd_legacy.h -o zstddeclib.c zstddeclib-in.c
```

`zstd.h` and `zstd_errors.h` are the release's, from `lib/`. BSD licence, in `LICENSE`.
