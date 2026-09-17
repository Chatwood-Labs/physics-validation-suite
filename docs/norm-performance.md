# Local norm performance measurements

Measured on Linux x86_64/glibc using CPython 3.12.3 and NumPy 2.2.6 from the
frozen lock. Both versions ran sequentially on the same host, with one warm-up
and five timed calls per workload. Allocation tracing used a separate call,
with input arrays allocated before tracing. Times are medians in milliseconds;
allocation figures are traced peak bytes, not process RSS.

| Workload | Elements | 0.2.6 ms | 0.2.7 ms | 0.2.6 peak bytes | 0.2.7 peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| profile | 64 | 0.041 | 0.033 | 4,416 | 4,792 |
| strided-field | 128 | 0.067 | 0.036 | 6,984 | 5,768 |
| profile | 200,000 | 76.613 | 17.007 | 8,025,856 | 3,402,082 |
| strided-field | 400,000 | 153.175 | 34.002 | 16,094,472 | 6,802,034 |
| profile | 1,000,000 | 389.409 | 81.120 | 40,450,528 | 17,002,082 |
| strided-field | 2,000,000 | 792.475 | 172.465 | 81,130,088 | 34,002,034 |

Every L1, L2 and L-infinity output matched exactly in hexadecimal. The small
64-element profile uses slightly more traced scratch allocation in 0.2.7;
large workloads show lower time and allocation cost. These measurements do
not establish production throughput, end-to-end solver performance, total
memory use or behavior on other hosts/dependency versions.

The profile workload compares a linear `[-1, 1]` array against zero. The
strided-field workload selects every other column of a four-column linear
field and compares it against 0.125. These exercise representative array
layouts, not a claim of coverage of every scientific workload.

Run this command separately under each installed release/environment:

```bash
python tools/benchmark-norms.py --sizes 64 200000 1000000 --repeats 5
```

The tool emits version/environment facts, raw timings, traced allocation peaks
and hexadecimal norms as JSON. The saved measurements and ratios are in
[norm-benchmark-0.2.7.json](norm-benchmark-0.2.7.json). Exact numerical conformance,
not a machine-specific timing threshold, is the automated correctness gate.
