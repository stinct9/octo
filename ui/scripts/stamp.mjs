// Writes dist/.src-hash: a hash of ui/src contents. `bench serve` recomputes it and refuses to
// start if the build is stale. Content-based on purpose: git checkouts rewrite file timestamps,
// which made a timestamp check report "stale" for identical sources.
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const root = new URL('..', import.meta.url).pathname
const src = join(root, 'src')
const files = []
const walk = (d) => readdirSync(d).forEach((n) => { const p = join(d, n); statSync(p).isDirectory() ? walk(p) : files.push(p) })
walk(src)
const h = createHash('sha256')
for (const f of files.map((f) => relative(src, f).split(sep).join('/')).sort()) {
  h.update(f)
  h.update(readFileSync(join(src, f)))
}
writeFileSync(join(root, 'dist', '.src-hash'), h.digest('hex'))
