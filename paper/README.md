# When Should an Agent Write a Program?

**Cost and Context of Code-Mode Tool Calling**
Mehmet Öner Yalçın, Fugu AI. October 2026.

Read it: [`main.pdf`](main.pdf)

This is a working paper. It has not been peer reviewed or submitted
anywhere.

## What it measures

In one production client (Claude Code), the paper compares tools registered
natively against the same tools called from a program through Toolplane. The
headline study is a pre-registered matrix of 240 runs. Its scope is one
client and a small number of runs per cell, and the paper treats the result
as a measured boundary for that system, not a general law.

## Evidence

Every number in the paper comes from rows committed under
[`bench/results/`](../bench/results/). Appendix B of the paper maps each
result to its run file. The registrations and analyses are:

| Study | Registration | Analysis |
|---|---|---|
| Confirmation matrix | [`bench/PREREG-confirmation-2026-10.md`](../bench/PREREG-confirmation-2026-10.md) | [`prereg-confirmation-2026-10-analysis.md`](../bench/results/prereg-confirmation-2026-10-analysis.md) |
| Persistence | [`bench/PREREG-persistence-2026-10.md`](../bench/PREREG-persistence-2026-10.md) | [`prereg-persistence-2026-10-analysis.md`](../bench/results/prereg-persistence-2026-10-analysis.md) |
| Store teaching | [`bench/PREREG-store-teaching-2026-10.md`](../bench/PREREG-store-teaching-2026-10.md) | [`prereg-store-teaching-2026-10-analysis.md`](../bench/results/prereg-store-teaching-2026-10-analysis.md) |

The longer engineering history behind these studies is in
[`docs/code-mode-benchmark.md`](../docs/code-mode-benchmark.md).

## Build

From this directory:

```bash
pdflatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

## Cite

See [`CITATION.cff`](../CITATION.cff) at the repository root.
