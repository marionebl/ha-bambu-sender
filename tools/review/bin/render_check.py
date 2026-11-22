#!/usr/bin/env python3
"""Render check.png (top + isometric) purely from plate.json + plate.bin.txt."""
import base64, json, sys, os
import numpy as np
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
d = sys.argv[1]
P = json.load(open(os.path.join(d, 'plate.json')))
buf = base64.b64decode(open(os.path.join(d, 'plate.bin.txt')).read())
N = P['segments']; b = P['bin']
co = np.frombuffer(buf, '<u2', N * 4, b['coords']).reshape(N, 4) / P['q']
ob = np.frombuffer(buf, 'u1', N, b['obj'])
zs = np.repeat([l['z'] for l in P['layers']], [l['n'] for l in P['layers']])
cm = plt.get_cmap('tab20')
cols = np.array([cm(i % 20) for i in range(len(P['objects']))]); cols[0] = (0.4, 0.4, 0.4, 1)
fig = plt.figure(figsize=(18, 9), dpi=110)
ax = fig.add_subplot(1, 2, 1)
segs = co.reshape(N, 2, 2)
ax.add_collection(LineCollection(segs, colors=cols[ob], linewidths=0.35))
x0, y0, x1, y1 = P['meta']['bed']
ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], 'k-', lw=0.8)
for i, o in enumerate(P['objects'][1:], 1):
    bx = o['bbox']; ax.text((bx[0] + bx[3]) / 2, (bx[1] + bx[4]) / 2, o['name'], fontsize=6, ha='center', color='k')
ax.set_xlim(x0 - 5, x1 + 5); ax.set_ylim(y0 - 5, y1 + 5); ax.set_aspect('equal'); ax.set_title('Top view, %d layers, N=%d segments' % (len(P['layers']), N))
ax2 = fig.add_subplot(1, 2, 2)
m = np.arange(N) % 3 == 0
a = np.radians(35); 
def proj(x, y, z):
    xr = x * np.cos(a) - y * np.sin(a); yr = (x * np.sin(a) + y * np.cos(a)) * 0.5 + z * 0.9
    return np.stack([xr, yr], -1)
p0 = proj(co[m, 0], co[m, 1], zs[m]); p1 = proj(co[m, 2], co[m, 3], zs[m])
ax2.add_collection(LineCollection(np.stack([p0, p1], 1), colors=cols[ob[m]], linewidths=0.3))
ax2.autoscale(); ax2.set_aspect('equal'); ax2.set_title('Isometric (every 3rd segment)')
for a_ in (ax, ax2): a_.tick_params(labelsize=7)
fig.legend(handles=[plt.Line2D([], [], color=cols[i], lw=3) for i in range(len(P['objects']))], labels=[o['name'] for o in P['objects']], loc='lower center', ncol=7, fontsize=7)
fig.savefig(os.path.join(d, 'check.png'), bbox_inches='tight')
