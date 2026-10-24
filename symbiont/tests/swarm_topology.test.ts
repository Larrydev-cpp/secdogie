// The isolation topology on the Workers core: the core at the edges, the user
// in the inner, failover inward, warm standbys for gap-free switching, and the
// user never on an edge -- the same roles the Python core decides.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { NodeRegistry } from '../src/swarm/registry.ts';
import { LineTopology } from '../src/swarm/topology.ts';

function line(n: number, edgeDepth = 1): { topo: LineTopology; reg: NodeRegistry; nodes: string[] } {
  const reg = new NodeRegistry();
  const topo = new LineTopology(reg, { edgeDepth });
  const nodes = Array.from({ length: n }, (_, i) => `did:key:n${i}`);
  for (const d of nodes) {
    reg.heartbeat(d, 0, 0.1);
    topo.place(d);
  }
  return { topo, reg, nodes };
}

test('core at the edges, user in the inner', () => {
  const { topo, nodes: n } = line(5);
  assert.deepEqual(topo.edges(), [n[0], n[4]]);
  assert.deepEqual(topo.inner(), [n[1], n[2], n[3]]);
  assert.equal(topo.userEntry(), n[2]); // most central
  assert.ok(topo.userIsIsolatedFromCore());
});

test('a failed edge moves inward', () => {
  const { topo, reg, nodes: n } = line(5);
  for (let i = 0; i < 3; i++) reg.record(n[0]!, false, 1 + i);
  assert.equal(reg.status(n[0]!), 'dead');
  assert.deepEqual(topo.edges(), [n[1], n[4]]);
});

test('warm edges hold a standby per side and failover is gap-free', () => {
  const { topo, reg, nodes: n } = line(6, 2);
  assert.deepEqual(topo.edges(), [n[0], n[1], n[4], n[5]]);
  assert.deepEqual(topo.standbys(), [n[1], n[4]]);
  assert.deepEqual(topo.inner(), [n[2], n[3]]);
  assert.ok(topo.userIsIsolatedFromCore());
  // kill the outer edge -- the standby was already attached, so no gap
  for (let i = 0; i < 3; i++) reg.record(n[0]!, false, 1 + i);
  assert.equal(reg.status(n[0]!), 'dead');
  assert.ok(topo.edges().includes(n[1]!));
});

test('edge depth never eats the inner buffer', () => {
  const { topo, nodes: n } = line(4, 2);
  assert.deepEqual(topo.edges(), [n[0], n[3]]); // reduced to depth 1
  assert.deepEqual(topo.inner(), [n[1], n[2]]);
  assert.notEqual(topo.userEntry(), null);
  assert.ok(topo.userIsIsolatedFromCore());
});

test('no inner buffer means no user entry', () => {
  const { topo, nodes: n } = line(2);
  assert.deepEqual(topo.edges(), [n[0], n[1]]);
  assert.deepEqual(topo.inner(), []);
  assert.equal(topo.userEntry(), null);
});
