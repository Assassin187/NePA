# RFC→Spec fixture boundary

This location is reserved for RFC→Spec front-end fixtures and is intentionally isolated from
`gold_file/http` and `gold_file/mqtt`, which remain the Spec→Code v3.0 coder fixtures.

There are currently no protocol fixtures here. If one is added, it may contain a frozen text
source, SourceSnapshot metadata, Spec IR v4.0, evidence, gaps, review record, and a generated
v3.0 projection. RFC tests must not import or mount the coder gold directories, and coder tests
must not discover this directory.
