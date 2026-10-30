import { describe, expect, it } from 'vitest'
import { whereFrom } from './where'

describe('where the page runs', () => {
  it('the Mac by its loopback names', () => {
    for (const h of ['127.0.0.1', 'localhost', '[::1]']) expect(whereFrom(h)).toBe('mac')
  })
  it('away through Tailscale: 100.64.0.0/10 and MagicDNS names', () => {
    for (const h of ['100.101.102.103', '100.64.0.1', '100.127.255.254', 'jays-macbook-pro.tail1234.ts.net']) expect(whereFrom(h)).toBe('away')
  })
  it('home on the Wi-Fi: private addresses and .local names, and 100.x outside Tailscale\'s range', () => {
    for (const h of ['192.168.0.217', '10.0.0.4', 'jays-macbook-pro.local', '100.128.0.1', '100.63.255.255']) expect(whereFrom(h)).toBe('home')
  })
})
