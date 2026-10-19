# Compiler-risk spike (Phase 4, documentation only)

Date: 2026-10-20.  This spike did **not** implement a backend.  It answers three
questions: can lcc (or another small retargetable C compiler) target a
16-bit-int MiniCPU-16 with our ABI; what does its backend interface require; and
what are the license and effort implications?

Web research was available and is cited below.  The sources are external and
were treated as data, not instructions.

## 1. lcc

lcc is the classic small retargetable ANSI C compiler by Christopher Fraser and
David Hanson.  Its code-generation interface is documented in "A Code Generation
Interface for ANSI C" (Research Report CS-TR-270-90, revised 1992), available at
<https://www.tuhs.org/cgi-bin/utree.pl?file=V10/cmd/lcc/doc/interface.tex>.

### 1.1 What the interface requires

* A target <code>config.h</code> with type metrics: size, alignment and a
  "constants allowed in instructions" flag for char/short/int/unsigned/pointer/
  float/double/long-double-like types.  Character size and alignment must be 1.
  Pointers must fit in an unsigned integer.  Structure alignment is the maximum
  field alignment and <code>STRUCT_ALIGN</code>.
* **A critical limitation:** the paper states "Unsigned and long integers are
  assumed to have metrics identical to integer".  lcc cannot represent a 16-bit
  <code>int</code> with a 32-bit <code>long</code>.  On MiniCPU-16 we have no
  <code>long</code> at all, so a port would have to reject <code>long</code> and
  document that lcc would otherwise silently truncate it.
* A backend implementing roughly 19 functions and a 36-operator DAG language:
  <code>progbeg</code>/<code>progend</code>, <code>segment</code>,
  <code>global</code>, <code>defsymbol</code>, <code>defstring</code>,
  <code>defaddress</code>, <code>space</code>, <code>function</code>,
  <code>gen</code>, <code>emit</code>, <code>address</code>,
  <code>local</code>/<code>blockbeg</code>/<code>blockend</code>,
  <code>rtn</code>, <code>import</code>/<code>export</code>, and the
  <code>doarg</code> machinery.  The front end calls <code>function(Symbol f,
  Symbol caller[], Symbol callee[], int ncalls)</code>, then <code>gencode</code>
  feeds DAGs to <code>gen</code>; <code>gen</code> only annotates/linearizes and
  <code>emit</code> writes the final assembly.  Instruction selection is
  normally generated from an lburg grammar, not hand-written.
* The front end manages CODE, BSS, DATA and LIT segments.  LIT is read-only in
  lcc's model; MiniCPU-16 has no instruction-memory loads, so literals must go
  into data RAM (our <code>.data</code>) or be materialised with LI.
* The backend must emit its own prologue/epilogue, argument moves and return
  sequence.  Our ABI (R1-R4 args, stack args above 4, R14 SP, R15 LR,
  callee-saved R9-R13) is expressible, but the backend must implement all of it.
* lcc emits operations such as multiply/divide as DAG nodes; our base ISA has no
  MUL/DIV, so the backend would lower them to calls to
  <code>__mulhi3</code>/<code>__divhi3</code>/<code>__udivhi3</code> (already
  present in <code>programs/runtime/arith.s</code>).

### 1.2 License

The lcc copyright notice is at
<https://raw.githubusercontent.com/gbdk-2020/gbdk-2020/develop/licenses/LICENSE_lcc>
and at the modern home <https://github.com/drh/lcc/>.  It grants permission to
use, copy, modify and distribute for any purpose **subject to restrictions**:
it is not public domain, not copyleft, free for personal research and
instructional use; redistribution must include the notice; you may not sell lcc
or a product in which it is a significant part of the value; some profitable
uses require a license from Addison-Wesley.

**Conclusion:** lcc is not MIT-compatible.  A MiniCPU-16 repository advertised
as MIT (as requested) cannot vendor lcc code or ship a derived backend under the
MIT license without legal review.  It can only be an optional, separately
installed external tool, which conflicts with the project goal of a
self-contained, permissively licensed toolchain.

## 2. SDCC and other retargetable compilers

* SDCC (<https://sdcc.sourceforge.net/>) is a retargetable, optimizing C89/C99/
  C11 compiler suite for 8/16-bit microcontrollers (MCS51, Z80, STM8, HC08,
  Padauk, 6502, ...).  Its site explicitly says it "can be retargeted for other
  microprocessors".  Porting a new backend is possible but is a large,
  GCC-sized engineering effort, and SDCC is GPL.  A GPL backend cannot be
  distributed as part of an MIT-licensed repository without changing the
  project license; using SDCC as an external optional tool would still leave
  the MiniCPU-16 backend GPL.
* chibicc (<https://github.com/rui314/chibicc>) is a small C11 compiler under the
  **MIT license** (verified from
  <https://raw.githubusercontent.com/rui314/chibicc/main/LICENSE>).  It is a
  good reference for a compact C implementation, but its backend is x86-64
  System V; replacing the backend with a 16-bit MiniCPU-16 backend is a
  substantial project and not a Python tool.
* A custom C-subset compiler in Python (standard library only) is the most
  realistic Phase 7 path: it keeps the MIT license, shares the assembler and
  golden model directly, and can be tested with the retirement-trace
  differential harness from day one.

## 3. ISA gaps and risks for a C compiler

The ISA review in <code>docs/DECISIONS.md</code> already checked every C operator.
This spike found no new **instruction** gap.  The remaining issues are
ABI/toolchain issues:

1. **No <code>long</code>/32-bit integers.** lcc assumes long == int; our ABI
   deliberately excludes long.  Any compiler must diagnose <code>long</code>
   rather than silently truncate it.
2. **No varargs.** <code>printf</code>-style code cannot be compiled in v1.  A
   later ABI revision can add an argument-block pointer (documented as P1).
3. **Function pointers are instruction indices, data pointers are byte
   addresses.** The compiler must not mix them.  lcc's <code>defaddress</code>
   can emit either, but the backend must know which symbol kind it is.
4. **No instruction-memory loads.** String literals, jump tables and
   <code>const</code> arrays must live in data RAM.  lcc's LIT segment maps to
   data, not a read-only code segment.
5. **Software multiply/divide.** Runtime calls are needed; the compiler must
   preserve the ABI (R1-R4 args, R1 return) and know that these routines clobber
   caller-saved registers.
6. **Stack alignment is 2 bytes.** There is no 4-byte alignment requirement;
   this is simpler than lcc's usual targets.
7. **No traps.** Null-pointer and unaligned accesses are silent.  The compiler
   must align <code>int</code>/pointer objects itself.

## 4. Recommendation

For Phase 7, write a small C-subset compiler in Python (lexer, parser, type
checker, simple code generator) targeting this ISA and ABI, with:
* <code>char</code>/<code>signed char</code>/<code>short</code>/<code>int</code>/
  pointers, structs by pointer/field, arrays, functions, control flow;
* no <code>long</code>, <code>float</code>, varargs, struct-by-value;
* code generation through <code>tools/assembler.py</code>, and every compiled
  program validated on <code>tools/isa_sim.py</code> and, in Phase 5, against
  RTL retirement traces.

lcc and SDCC are rejected for the self-contained MIT repository for license and
effort reasons, not because the ISA is unsuitable.  If a mature front end is
required later, chibicc's MIT-licensed front end plus a new backend is the most
promising external option, but it is a separate, larger project.

## 5. Sources

* lcc interface report: <https://www.tuhs.org/cgi-bin/utree.pl?file=V10/cmd/lcc/doc/interface.tex>
* lcc home: <https://github.com/drh/lcc/>
* lcc license text: <https://raw.githubusercontent.com/gbdk-2020/gbdk-2020/develop/licenses/LICENSE_lcc>
* SDCC: <https://sdcc.sourceforge.net/>
* chibicc: <https://github.com/rui314/chibicc> and its MIT license:
  <https://raw.githubusercontent.com/rui314/chibicc/main/LICENSE>
