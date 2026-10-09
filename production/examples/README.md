# Examples

## Is there a reproducible example I can run without private infrastructure?

No. We looked for one honestly and there is not: every path through `/supervise` v2.8.2 requires the private staffing router and the operator's harness installation. Rather than ship something that pretends to run, this directory contains a **sanitized walkthrough of the accepted run**, with the real PLAN shape, the real sequence of starts, the real finding and the real closure records, and nothing that identifies the host.

- [scrap-suno-scan-gate](scrap-suno-scan-gate/README.md): the v2.8.2 acceptance run (October 7, 2026): author, independent review with a genuine finding, two repair starts, final acceptance, five starts, 391.575 s, closed ACCEPTED.

When a source release exists (see [installation and configuration](../docs/install-and-configuration.md)), the first thing it will carry is a runnable version of this example on a host with no access to the author's estate.
