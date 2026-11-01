# C and C++ Audit Pack

## Contents

- [Applicability](#applicability)
- [Map native trust boundaries](#map-native-trust-boundaries)
- [Prioritize memory-safety review](#prioritize-memory-safety-review)
- [Parsing and numeric invariants](#parsing-and-numeric-invariants)
- [C-specific review](#c-specific-review)
- [C++-specific review](#c-specific-review-1)
- [Concurrency, signals, and callbacks](#concurrency-signals-and-callbacks)
- [Platform and privilege boundaries](#platform-and-privilege-boundaries)
- [Injection, files, network, and cryptography](#injection-files-network-and-cryptography)
- [Build and configuration review](#build-and-configuration-review)
- [Defense validation](#defense-validation)
- [Tool-assisted checks](#tool-assisted-checks)

## Applicability

Use this pack for C, C++, Objective-C/C++, embedded/native modules, libraries,
drivers, parsers/codecs, language runtimes, desktop software, plugins, and FFI/ABI
boundaries.

Distinguish:

- production, test, example, benchmark, fuzz, generated, vendored, and platform-only
  code;
- C from C++, the exact language standard, compiler, target ABI, architecture,
  endianness, and build flags;
- process-level inputs from trusted in-process APIs whose callers may still pass
  adversarial values;
- memory corruption root cause from outcomes such as crash, code execution, data
  disclosure, or privilege escalation;
- proof from a sanitizer/static analyzer from general reachability and impact.

## Map native trust boundaries

Find:

1. Network packets, RPC/IPC, files/media/documents, archives, devices/IOCTLs,
   environment, command-line arguments, plugins, callbacks, and foreign-language
   entry points.
2. Parsers/decoders and every length, offset, count, type tag, dimension, recursion,
   allocation, and cross-field invariant.
3. Ownership/lifetime boundaries for buffers, objects, handles, callbacks,
   asynchronous work, containers, views, and references.
4. Privilege transitions, sandbox boundaries, service/plugin boundaries, kernel/user
   crossings, and shared memory.
5. Filesystem/process/network/query/template/format-string sinks and dynamic loading.
6. Cryptographic verification, key material, random generation, certificate/TLS,
   update/plugin integrity, and secret clearing.
7. Threads, atomics, locks, signals, interrupts, event loops, coroutines, and
   teardown.
8. CMake/Meson/Autotools/build scripts, feature macros, build types, hardening,
   generated code, dependencies, and platform-specific implementations.

Start with externally reachable parsers and privileged components; they combine
attacker control with the highest-impact memory and logic sinks.

## Prioritize memory-safety review

### Bounds and buffer operations

- Trace the origin, signedness, type width, unit, and validation of every length,
  index, offset, stride, dimension, and element count.
- Check reads and writes separately; an out-of-bounds read can disclose secrets or
  steer later control flow even when no write occurs.
- Review array/pointer indexing, iterators, spans, string operations,
  `memcpy`/`memmove`/`memset`, formatted I/O, encoding conversion, and manual
  serialization.
- Confirm destination capacity, source availability, overlap requirements,
  terminators, alignment, and element-vs-byte units at the final operation.
- Check zero-length, empty, maximum, truncated, negative, wraparound, and
  one-past-end cases.
- Treat a prior check as valid only if no conversion, arithmetic, reallocation,
  mutation, or concurrent change invalidates it.

### Integer and size arithmetic

- Check overflow/underflow before addition/multiplication/shift and before converting
  to `size_t`, pointer offsets, allocator sizes, loop bounds, or system-call types.
- Signed overflow is undefined behavior; unsigned wrap is defined but can still
  bypass bounds and produce undersized allocations.
- Review narrowing, sign changes, enum conversions, varint/length decoding,
  `sizeof` unit assumptions, and differences between 32/64-bit targets.
- Verify the multiplication/addition used for allocation is the same validated value
  used for access.
- Check `calloc`/container capacity/growth calculations and allocation failure paths.

### Lifetime, ownership, and invalidation

- Identify the owner and destruction point of every allocation, handle, object,
  callback context, and asynchronous operation.
- Review use-after-free across callbacks, queued work, timers, cancellation,
  container mutation, plugin unload, reference counting, shutdown, and error cleanup.
- Check double/invalid free, mismatched allocator/deallocator, `new` vs `delete[]`,
  ownership transfer, partial initialization, and duplicated cleanup.
- Treat raw pointers/references, iterators, `std::span`, `std::string_view`, and
  borrowed handles as lifetime-sensitive even when the original owner uses RAII.
- Review shared ownership cycles, custom deleters, aliasing smart pointers, weak
  pointer promotion, and concurrent final release.

### Initialization, type, and object representation

- Check uninitialized scalars, padding, union/variant members, partially initialized
  structs, failed constructors/factories, and output parameters.
- Look for sensitive padding/uninitialized memory copied to the network, files, IPC,
  logs, hashing/signatures, or another privilege boundary.
- Review downcasts, C-style/reinterpret casts, tagged unions, polymorphic
  deserialization, type punning, strict aliasing, placement new, object lifetime,
  and ABI/layout disagreement.
- Validate format strings separately from format arguments. External control of the
  format string can read/write memory even when argument buffers are bounded.

## Parsing and numeric invariants

For each parser/decoder:

1. Establish the authoritative total input length and cursor representation.
2. Verify every read has an overflow-safe `needed <= remaining` style check.
3. Check offsets/ranges for overflow and containment before pointer construction.
4. Validate counts/dimensions before allocation, loops, recursion, or multiplication.
5. Enforce cross-field ordering, overlap, uniqueness, type, version, and consistency.
6. Bound recursion, nesting, decompression/expansion, work factor, and output size.
7. Check endianness, alignment, packed structs, bitfields, and unaligned access on
   every supported target.
8. Handle truncation, trailing bytes, duplicate sections, unknown tags, and
   error/partial-object cleanup safely.

Checksums, magic values, and successful parsing do not make an input trusted.
Cryptographic authenticity/provenance and semantic authorization are separate.

## C-specific review

- Make ownership conventions explicit for returned pointers, output parameters,
  caller-provided buffers, and cleanup labels.
- Review `goto cleanup` and multi-stage initialization for double free, leaks,
  use-before-init, and cleanup of resources never acquired.
- Check flexible array members, struct trailing data, variable-length arrays,
  `alloca`, stack recursion, and attacker-controlled stack allocation.
- Review `strcpy`/`strcat`/`sprintf` and bounded variants. A bounded API can still
  omit terminators, truncate security identifiers, use the wrong capacity, or hide
  arithmetic overflow.
- Check `scanf` families, tokenization, locale-dependent conversion, `errno`,
  partial numeric parsing, and accepted trailing input.
- Review function-pointer tables, callbacks, user data, signal handlers, `setjmp`/
  `longjmp`, and manual reference counting.
- Check allocation-zeroing assumptions, `realloc` failure/alias handling, and
  zero-size allocation portability.

## C++-specific review

- Verify RAII actually owns every resource on all exception, early-return,
  coroutine, and partially constructed paths.
- Review references/views/iterators captured by lambdas, callbacks, coroutines,
  futures, and threads after their owners or containers can move/die/reallocate.
- Check `std::move` use, moved-from object assumptions, forwarding references,
  temporary lifetime, initializer-list lifetime, and return/capture by reference.
- Review container invalidation rules and concurrent access to strings, vectors,
  maps, unordered containers, and shared state.
- Check `std::string_view`/`std::span` construction from temporaries, local buffers,
  mutable storage, memory maps, or network receive buffers.
- Review smart-pointer ownership: raw observers outliving owners, `shared_ptr`
  cycles, aliasing constructors, custom deleters, double ownership of one raw
  pointer, and `enable_shared_from_this` misuse.
- Validate variant/optional/expected/error handling before access. Exceptions,
  status codes, and sentinel values must preserve the same invariant.
- Review serialization/reflection/factory registries, RTTI/downcasts, plugins, and
  attacker-controlled type selection.
- Check operator overloading, implicit conversions, comparator strict ordering,
  hash/equality consistency, and parsing constructors for security-sensitive types.
- Treat concepts/templates/static types as compile-time constraints, not validation
  of untrusted runtime bytes.

## Concurrency, signals, and callbacks

- A data race in C/C++ is undefined behavior, not merely a stale-read issue. Prioritize
  races affecting object lifetime, bounds, identity, authorization, and output.
- Review lock coverage/order, atomic vs non-atomic access, memory ordering, condition
  predicates, double-checked initialization, and teardown.
- Verify threads, tasks, callbacks, timers, coroutines, and event registrations
  cannot outlive captured objects or plugin/module code.
- Check cancellation and timeout for concurrent free/close, duplicated completion,
  partially committed state, and callback-after-destruction.
- Use async-signal-safe operations in signal handlers; review shared flags/types,
  reentrancy, interrupted syscalls, and handler installation/removal.
- For embedded/interrupt code, review volatile vs atomic semantics, ISR/task shared
  state, DMA buffer lifetime, memory barriers, priority inversion, and watchdog
  behavior.
- Verify check-and-update business/security state at the durable/shared boundary,
  not with a process-local lock when multiple processes/hosts participate.

## Platform and privilege boundaries

### POSIX/Unix

- Review setuid/setgid/capability transitions, supplementary groups, privilege drop
  order, saved IDs, chroot/namespaces, inherited descriptors, umask, and environment.
- Check temporary files, symlink/hardlink races, file descriptor vs pathname checks,
  `/proc`/device access, permissions, and attacker-controlled current directories.
- Review signal, fork/exec in multi-threaded processes, shell use, PATH/library
  search, locale, and loader environment variables.

### Windows

- Review ANSI/Unicode conversion, path/device/UNC/alternate-stream semantics,
  canonicalization, ACLs, impersonation tokens, handles, and inherited objects.
- Check DLL/executable search order, unquoted service paths, current directory,
  side-by-side loading, environment expansion, and writable lookup locations.
- Review COM/RPC/named-pipe caller identity, service/session boundaries, object
  namespaces, registry access, and custom protocol handlers.

### Plugins, sandboxes, and FFI

- Verify plugin/module origin and integrity, search paths, ABI/version compatibility,
  lifecycle, callbacks, permissions, and unload with active objects/threads.
- Treat all FFI inputs/outputs as untyped: verify lengths, ownership, allocation,
  exceptions/panics, callbacks, thread affinity, and object lifetime on both sides.
- Review sandbox host calls, capabilities, shared memory, file descriptors/handles,
  mounts, syscalls, and confused-deputy behavior separately from memory safety.

## Injection, files, network, and cryptography

Native memory review does not replace general application-security review:

- Trace SQL/query, shell/command/argument, format-string, template, logging, and
  dynamic-code inputs to their interpreters.
- Review path traversal, absolute paths, links/races, uploads, archives, temporary
  files, permissions, search-path/library hijacking, and arbitrary file operations.
- Check SSRF/proxy/webhook/update URL handling, redirects, DNS, resolved endpoints,
  proxy settings, credentials, and timeouts.
- Verify authentication/authorization/tenant checks at each privileged API,
  object/resource lookup, IPC method, plugin callback, and administrative path.
- Review cryptographic algorithms/modes, randomness, key/nonce lifecycle,
  certificate/hostname verification, signatures/MACs, update integrity, and
  constant-time secret comparison.
- Use a clearing primitive the compiler/platform guarantees will not optimize away
  for secrets; confirm copies, allocator reuse, core dumps, logs, and crash reports.
- Check all security-library return codes and distinguish verification failure from
  operational errors without failing open.

## Build and configuration review

- Determine the exact production compiler, standard, target, build type, feature
  macros, build tags/options, generated sources, and dependency versions.
- Compare debug/test/fuzz builds with release behavior: `NDEBUG`, assertions,
  integer checks, exceptions/RTTI, logging, allocator, and hardening may differ.
- Inspect warnings, optimization/LTO, stack protection, fortification, control-flow
  protection, PIE/ASLR compatibility, RELRO/NX, symbol visibility, and runtime
  sandboxing as defense-in-depth—not substitutes for root-cause fixes.
- Review CMake/Meson/Autotools/custom commands, downloads, package discovery,
  toolchain files, environment paths, code generators, and install/RPATH behavior.
- Check generated parser/serialization code and compile definitions rather than
  auditing only templates/source schemas.
- Establish which vendored/static/shared dependency implementation is linked in the
  deployed artifact before mapping advisories.

Undefined behavior allows the optimizer to invalidate intuitive source-level
reasoning. Checks performed after an overflow, invalid pointer creation, lifetime
violation, or data race may not protect the optimized program.

## Defense validation

Accept a defense only after confirming:

- all size/offset/index arithmetic is overflow-safe in the actual types before
  allocation, pointer formation, and access;
- bounds checks use the authoritative buffer/range and survive conversions,
  mutation, reallocation, links, and concurrency;
- ownership and lifetime include callbacks, asynchronous work, views, container
  invalidation, errors, shutdown, and foreign runtimes;
- parser limits cover input bytes, expanded output, depth, count, work, allocation,
  and semantic cross-field invariants;
- format strings are constant/trusted and argument types match;
- filesystem/process/network/interpreter inputs receive context-specific
  containment, allowlisting, parameterization, and authorization;
- security checks do not rely on `assert`, undefined behavior, debug-only code, or
  sanitizer instrumentation;
- compiler/library/platform behavior and hardening match the deployed artifact.

Do not report:

- a raw pointer, cast, `memcpy`, C string API, manual allocation, or unchecked-looking
  operation without a reachable invariant violation;
- `strncpy`/smart pointers/containers as automatically safe or automatically unsafe;
- a sanitizer/static-analysis warning without confirming the real path and impact;
- a crash as code execution without evidence that memory corruption yields that
  control;
- missing hardening flags as the root-cause vulnerability when an underlying defect
  has not been established;
- a dependency advisory without resolving the linked version, configuration,
  platform, and relevant reachable feature.

## Tool-assisted checks

Compiling, sanitizer builds, fuzzing, and running tests or build scripts execute
project code; they run only with explicit user authorization, never on an untrusted
repository. Without authorization, list them as recommended checks:

- compile with configured warnings and treat new warnings as investigation seeds;
- AddressSanitizer and UndefinedBehaviorSanitizer for memory/UB paths;
- MemorySanitizer where the supported toolchain/build permits;
- ThreadSanitizer for concurrency paths;
- configured static analyzers such as clang-tidy, compiler analysis, or cppcheck;
- fuzz existing bounded parser/API harnesses with sanitizers;
- dependency/binary hardening inspection against the actual built artifact.

Sanitizers change timing/layout and cover only executed paths. A clean run does not
prove absence; a report still needs attacker control, reachability, root cause, and
impact.
