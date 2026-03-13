---
name: large-file-writes
description: Write large files in append chunks to avoid truncated tool-call arguments.
always: false
---

# Append Mode For Large Files

## Rules

1. Do not send very large content in one `write_file` call.
2. For long outputs, split content into small chunks (for example 600-1200 characters per call).
3. Create/overwrite with the first chunk using:
   - `write_file(path=..., content=..., append=false)`
4. Add remaining chunks using:
   - `write_file(path=..., content=..., append=true)`
5. After chunked writes, verify with `read_file` or `list_dir`.

## When To Use

- Reports, markdown docs, generated code, or data dumps that may exceed model output limits.
- Any case where tool arguments risk truncation.
