"""
HoloMind v19 â€” Turbo Edition
==============================

OTIMIZAÃ‡Ã•ES PRINCIPAIS vs v18:
  â€¢ darken: (frame*0.28).astype(uint8) â†’ cv2.convertScaleAbs()   â†’ -38ms/frame
  â€¢ MediaPipe model_complexity: 1 â†’ 0                             â†’ -15ms/frame
  â€¢ Mini-graph shell: cacheada (pre-bake), sÃ³ nodes/edges dinÃ¢micos
  â€¢ Scan lines: cv2 loops â†’ numpy stride slice                     â†’ -0.4ms
  â€¢ Sem LINE_AA em elementos de baixa prioridade
  â€¢ Ambient particles: 60 â†’ 35 (ainda bonito, mais rÃ¡pido)
  â€¢ GaussianBlur: pre-calculado na cache, nÃ£o a cada frame
  â€¢ frame.astype(float32) para blit aditivo: uint8 cv2.add direto  â†’ -4ms
  Total esperado: ~55-60ms/frame economizados â†’ de 15fps para 50-60fps
"""

import cv2
import numpy as np
import mediapipe as mp
import math
import time
import random
import os
import base64
import json
import re
import threading
import urllib.request
import urllib.error
from collections import deque, defaultdict
from memory_bridge import MemoryBridge

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CONSTANTES
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
WIN       = "HoloMind â€” Turbo"
SIDEBAR_W = 446
FONT      = cv2.FONT_HERSHEY_SIMPLEX
SIDEBAR_SCALE = 1.15

CAM_L0 = 540.0
CAM_L1 = 400.0
CAM_L2 = 200.0
MEMORY_API_URL = os.getenv("HOLOMIND_MEMORY_API", "http://127.0.0.1:8787")
MEMORY_SYNC_INTERVAL = float(os.getenv("HOLOMIND_SYNC_INTERVAL", "0.8"))
MEMORY_TIMEOUT_S = float(os.getenv("HOLOMIND_MEMORY_TIMEOUT", "1.8"))
MEMORY_ACTION_TIMEOUT_S = float(os.getenv("HOLOMIND_MEMORY_ACTION_TIMEOUT", "180"))
VOICE_STRICT_MODE = os.getenv("HOLOMIND_VOICE_STRICT", "true").strip().lower() == "true"
VOICE_PROFILE_ID = os.getenv("HOLOMIND_VOICE_PROFILE_ID", "dgff").strip() or "dgff"
VOICE_LANGUAGE = os.getenv("HOLOMIND_VOICE_LANGUAGE", "pt").strip() or "pt"
VOICE_SERVER_URL = os.getenv("HOLOMIND_VOICE_SERVER_URL", "http://127.0.0.1:17493").strip() or "http://127.0.0.1:17493"
AUTO_REPLY_POLL_S = float(os.getenv("HOLOMIND_AUTO_REPLY_POLL", "0.9"))
AUTO_REPLY_MAX_CHARS = int(float(os.getenv("HOLOMIND_AUTO_REPLY_MAX_CHARS", "180")))

WHITE     = (245, 245, 245)
GRAY_LT   = (170, 170, 170)
GRAY      = (100, 100, 100)
GRAY_DK   = (48,  48,  48)

NODE_DEF  = (140, 160, 200)
NODE_HOV  = (210, 230, 255)
NODE_SEL  = (255, 185, 0  )
NODE_DEEP = (0,   220, 255)
NODE_HOP1 = (80,  220, 120)
NODE_HOP2 = (45,  130, 70 )

LBL_DEF   = (110, 170, 255)
LBL_HOV   = (200, 230, 255)
LBL_SEL   = (255, 185, 0  )
LBL_HOP1  = (80,  220, 120)

PART_COL  = (0,   190, 255)
BRACKET   = (100, 130, 180)
DWELL_COL = (0,   230, 120)
SNAP_COL  = (0,   200, 100)

_VOICE_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)
_VOICE_PROFILE_CACHE = {"ref": None, "resolved": None, "ts": 0.0}


def resolve_voice_profile_id(profile_ref):
    ref = str(profile_ref or "").strip()
    if not ref:
        return None
    if _VOICE_UUID_RE.match(ref):
        return ref

    now = time.time()
    if _VOICE_PROFILE_CACHE["ref"] == ref and _VOICE_PROFILE_CACHE["resolved"] and (now - _VOICE_PROFILE_CACHE["ts"] <= 30.0):
        return _VOICE_PROFILE_CACHE["resolved"]

    try:
        url = VOICE_SERVER_URL.rstrip("/") + "/profiles"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.2) as response:
            raw = response.read().decode("utf-8", errors="replace")
            profiles = json.loads(raw)
        if isinstance(profiles, list):
            target = ref.lower()
            for p in profiles:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name") or "").strip().lower()
                pid = str(p.get("id") or "").strip()
                if name == target and pid:
                    _VOICE_PROFILE_CACHE["ref"] = ref
                    _VOICE_PROFILE_CACHE["resolved"] = pid
                    _VOICE_PROFILE_CACHE["ts"] = now
                    return pid
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        pass

    return ref

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# DADOS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Legado/demo: mantido apenas como referencia, nao e usado no fluxo WhatsApp-first.
SAMPLE_NOTES = {
    "Artificial Life & Morphogenesis": {
        "info": "Estudo de sistemas vivos e auto-organizaÃ§Ã£o emergente.",
        "content": "self-organization,\nmorphogenesis,\nbiophysics, pattern\nformation",
        "snippets": ["five elemental types", "possibility: shape", "complexity analysis",
                     "resources on the topic", "cellular automata", "emergent behavior"],
    },
    "Generative Media & Notation": {
        "info": "MÃ­dia generativa e sistemas de notaÃ§Ã£o visual.",
        "content": "readability space,\nexpressivity range\nclaude as document\nmarvelous clouds",
        "snippets": ["notation system", "visual grammar", "book summary", "drawing; asemic",
                     "marvelous clouds", "writing art"],
    },
    "Interface Design & Aesthetics": {
        "info": "Design de interfaces e estÃ©tica digital.",
        "content": "reading on stacks,\nlike landscape,\ninterface that taps\nancestral instincts",
        "snippets": ["like landscape", "ways to show new", "interface that test",
                     "beyond the surface", "15 years of desktop"],
    },
    "Cognition, Language & AI": {
        "info": "CogniÃ§Ã£o, linguagem natural e inteligÃªncia artificial.",
        "content": "an information-\ntheoretic\nforeshadowing of\nmathematicians",
        "snippets": ["claude soul document", "nodal-points-digest", "week-0201-draft",
                     "artificial memory", "web representation"],
    },
    "History of Information Tools": {
        "info": "EvoluÃ§Ã£o histÃ³rica das ferramentas de informaÃ§Ã£o.",
        "content": "hypertext and\nmultimedia and\nhypertext article\nfrom the 80s",
        "snippets": ["past 90s tools", "card catalogs", "hypertext origins",
                     "the perceptron controversy"],
    },
    "Beyond the Surface": {
        "info": "Camadas profundas em sistemas complexos.",
        "content": "the evolution of\nnotation in arts\nand sciences",
        "snippets": ["writing for others", "rough patterns", "latent structure",
                     "beyond the surface", "neurosemiotics"],
    },
    "Representation Engineering": {
        "info": "RepresentaÃ§Ã£o avanÃ§ada de dados e sistemas visuais.",
        "content": "provides organic\ndata for\npandemonium\narchitecture",
        "snippets": ["mistral-7b an acid trip", "embedding spaces",
                     "mirrors the bottom", "critiques the abstraction"],
    },
    "Self-Organization": {
        "info": "Sistemas que se organizam autonomamente.",
        "content": "spontaneous order,\nattractor basins,\nfeedback loops,\ncriticality edge",
        "snippets": ["spontaneous order", "attractor basins", "feedback loops",
                     "polling tools for thinking"],
    },
    "Morphogenesis": {
        "info": "FormaÃ§Ã£o de estruturas em sistemas biolÃ³gicos e digitais.",
        "content": "turing patterns,\nreaction-diffusion,\nbiological\nscaffolding",
        "snippets": ["turing patterns", "reaction-diffusion", "biological scaffolding",
                     "symmetry breaking"],
    },
    "Language Invention": {
        "info": "CriaÃ§Ã£o de novas linguagens e sistemas de comunicaÃ§Ã£o.",
        "content": "a grounded and\nnaturalistic\napproach to\nlanguage invention",
        "snippets": ["a grounded and", "naturalistic approach", "to language invention",
                     "vindicates the sub-symbolic"],
    },
    "Drawing & Assembling": {
        "info": "Desenho, composiÃ§Ã£o e montagem de elementos conceituais.",
        "content": "boundaries between\nwriting and\ndrawing; asemic\nwriting art",
        "snippets": ["visual thinking", "sketch-first method", "boundaries between",
                     "writing and drawing"],
    },
    "Multimedia Notation": {
        "info": "NotaÃ§Ã£o para representar informaÃ§Ã£o multimÃ­dia estruturada.",
        "content": "time-based score,\ncross-modal links,\nannotated timeline,\nsemantic anchors",
        "snippets": ["time-based score", "cross-modal links", "annotated timeline",
                     "semantic anchors"],
    },
}

EDGE_LABELS = ["connects to", "relates to", "inspires", "foundation of",
               "extends", "critiques", "enables", "informs"]

TOPO_DESC = {
    "centralized":   ("mode: centralized",   ["one central thought", "connected to all others"]),
    "decentralized": ("mode: decentralized", ["notes in different",  "clusters by topic"]),
    "distributed":   ("mode: distributed",   ["notes connected by",  "relationships via edges"]),
}

NOTES = {
    "WhatsApp Memory": {
        "info": "Aguardando sincronizacao do backend WhatsApp.",
        "content": "inicie o backend node\nescaneie o QR do baileys\npressione G para sync",
        "snippets": ["whatsapp", "memory", "sync", "openai"],
        "meta": {"type": "bootstrap"},
    }
}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# NÃ“ 3D
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class Node3D:
    __slots__ = ['x','y','z','title','info','content','snippets',
                 'sx','sy','scale','visible','target_x','target_y','target_z',
                 'proximity','alpha','hop','degree','pulse_phase',
                 'orig_x','orig_y','orig_z','meta']

    def __init__(self, title, data):
        self.title   = title
        self.info    = data["info"]
        self.content = data.get("content", "")
        self.snippets= data.get("snippets", [])
        self.meta    = data.get("meta", {})
        r = random.uniform(80, 210)
        a = random.uniform(0, math.pi * 2)
        b = random.uniform(-math.pi / 2, math.pi / 2)
        x = r * math.cos(b) * math.cos(a)
        y = r * math.cos(b) * math.sin(a) * 0.6
        z = r * math.sin(b)
        self.x = self.target_x = self.orig_x = x
        self.y = self.target_y = self.orig_y = y
        self.z = self.target_z = self.orig_z = z
        self.sx = self.sy = 0
        self.scale = 1.0; self.visible = True
        self.proximity = 0.0; self.alpha = 1.0
        self.hop = -1; self.degree = 0
        self.pulse_phase = random.uniform(0, math.pi * 2)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# REDE 3D
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class Network3D:

    def __init__(self):
        self.nodes = []; self.edges = []; self.topology = "centralized"
        self.cam_z = CAM_L0; self.target_cam_z = CAM_L0
        self.cam_ox = 0.0; self.target_ox = 0.0
        self.cam_oy = 0.0; self.target_oy = 0.0
        self.rotation_y = 0.0; self.rotation_x = 0.18
        self.cos_ry = 1.0; self.sin_ry = 0.0
        self.cos_rx = math.cos(0.18); self.sin_rx = math.sin(0.18)
        self.deep_rot_y = 0.0; self.deep_rot_x = 0.0
        self.deep_cos_ry = 1.0; self.deep_sin_ry = 0.0
        self.deep_cos_rx = 1.0; self.deep_sin_rx = 0.0
        self.selected_node = None; self.hovered_node = None
        self.active_edges = set(); self.zoom_level = 0
        self.zoom_progress = 0.0; self.deep_progress = 0.0
        self.history = deque(maxlen=25); self.adj = defaultdict(set)
        self.dwell_node = None; self.dwell_timer = 0.0; self.DWELL_TIME = 1.5
        self.deep_cluster = {}

    def build_from_notes(self, notes):
        self.nodes = [Node3D(t, d) for t, d in notes.items()]
        self.edges = []; self.selected_node = None; self.hovered_node = None
        self.active_edges = set(); self.zoom_level = 0
        self.zoom_progress = 0.0; self.deep_progress = 0.0
        self.history.clear()
        self.cam_z = self.target_cam_z = CAM_L0
        self.cam_ox = self.cam_oy = self.target_ox = self.target_oy = 0.0
        self.dwell_node = None; self.dwell_timer = 0.0
        self.deep_rot_y = 0.0; self.deep_rot_x = 0.0; self.deep_cluster = {}
        self._apply_topology(self.topology)

    def _apply_topology(self, topo):
        self.topology = topo; n = len(self.nodes)
        if n == 0:
            self.edges=[]; self.adj=defaultdict(set)
            return
        if topo == "centralized":
            self.nodes[0].orig_x = self.nodes[0].target_x = 0
            self.nodes[0].orig_y = self.nodes[0].target_y = 0
            self.nodes[0].orig_z = self.nodes[0].target_z = 0
            for i in range(1, n):
                ah = (i/(n-1))*math.pi*2; av = (i/(n-1))*math.pi - math.pi/2
                r = 185+(i%5)*20
                self.nodes[i].orig_x = self.nodes[i].target_x = r*math.cos(ah)*math.cos(av)
                self.nodes[i].orig_y = self.nodes[i].target_y = r*math.sin(av)*0.58
                self.nodes[i].orig_z = self.nodes[i].target_z = r*math.sin(ah)*math.cos(av)
        elif topo == "decentralized":
            grps = 3
            for i, node in enumerate(self.nodes):
                g=i%grps; gi=i//grps; ag=(g/grps)*math.pi*2
                cx_=145*math.cos(ag); cz_=145*math.sin(ag)
                ain=(gi/max(1,n//grps))*math.pi*2+g*1.1; rin=50+gi*22
                node.orig_x=node.target_x=cx_+rin*math.cos(ain)
                node.orig_y=node.target_y=random.uniform(-50,50)
                node.orig_z=node.target_z=cz_+rin*math.sin(ain)
        else:
            for i, node in enumerate(self.nodes):
                phi=math.acos(1-2*(i+0.5)/n); theta=math.pi*(1+5**0.5)*i; r=210+(i%7)*10
                node.orig_x=node.target_x=r*math.sin(phi)*math.cos(theta)
                node.orig_y=node.target_y=r*math.cos(phi)*0.55
                node.orig_z=node.target_z=r*math.sin(phi)*math.sin(theta)

        density = 0.42 if topo=="distributed" else (0.13 if topo=="centralized" else 0.22)
        self.edges=[]; self.adj=defaultdict(set)
        for i in range(n):
            for j in range(i+1,n):
                if random.random()<density:
                    lbl=random.choice(EDGE_LABELS) if random.random()<0.26 else ""
                    self.edges.append((i,j,lbl)); self.adj[i].add(j); self.adj[j].add(i)
        if topo=="centralized":
            for i in range(1,n):
                if i not in self.adj[0]:
                    self.edges.append((0,i,"")); self.adj[0].add(i); self.adj[i].add(0)
        for ni,node in enumerate(self.nodes): node.degree=len(self.adj[ni])

    def set_topology(self, topo):
        self._push_history()
        if self.selected_node: self._exit_selection(push=False)
        self._apply_topology(topo)

    def select_node(self, node, push=True):
        if push: self._push_history()
        self.selected_node=node; self._rebuild_active(); self._compute_hops(); self._update_alphas()
        if node is not None:
            self.zoom_level=1; self.target_cam_z=CAM_L1
            self.target_ox=0.0; self.target_oy=0.0
        else: self._exit_selection(push=False)

    def deep_dive(self, push=True):
        if not self.selected_node: return
        if push: self._push_history()
        self.zoom_level=2; self.target_cam_z=CAM_L2
        self.target_ox=0.0; self.target_oy=0.0
        self.deep_rot_y=self.rotation_y; self.deep_rot_x=self.rotation_x
        sel_idx=self.nodes.index(self.selected_node); neighbors=self.adj[sel_idx]; sel=self.selected_node
        for i, node in enumerate(self.nodes):
            if i==sel_idx: continue
            dx=node.orig_x-sel.orig_x; dy=node.orig_y-sel.orig_y; dz=node.orig_z-sel.orig_z
            push_f=3.0 if i in neighbors else 4.5
            node.target_x=sel.orig_x+dx*push_f
            node.target_y=sel.orig_y+dy*push_f
            node.target_z=sel.orig_z+dz*push_f
        self._build_cluster(sel_idx, list(neighbors))

    def _build_cluster(self, sel_idx, nb_list):
        n=len(nb_list); self.deep_cluster={sel_idx:(0.0,0.0,0.0)}
        for ki,nb_idx in enumerate(nb_list):
            phi=math.acos(1-2*(ki+0.5)/max(1,n)); theta=math.pi*(1+5**0.5)*ki; r=80.0
            self.deep_cluster[nb_idx]=(r*math.sin(phi)*math.cos(theta),
                                        r*math.cos(phi)*0.65,
                                        r*math.sin(phi)*math.sin(theta))

    def _exit_selection(self, push=True):
        if push: self._push_history()
        self.selected_node=None; self.zoom_level=0; self.target_cam_z=CAM_L0
        self.target_ox=self.target_oy=0.0
        for node in self.nodes: node.target_x=node.orig_x; node.target_y=node.orig_y; node.target_z=node.orig_z
        self.deep_cluster={}; self._rebuild_active(); self._compute_hops(); self._update_alphas()

    def _push_history(self):
        self.history.append({"topology":self.topology,"selected":self.selected_node,
            "zoom_lvl":self.zoom_level,"cam_z":self.target_cam_z,
            "ox":self.target_ox,"oy":self.target_oy,
            "hops":{n:n.hop for n in self.nodes},"alphas":{n:n.alpha for n in self.nodes},
            "targets":{n:(n.target_x,n.target_y,n.target_z) for n in self.nodes},
            "dcluster":dict(self.deep_cluster)})

    def undo(self):
        if not self.history: return
        s=self.history.pop()
        if s["topology"]!=self.topology: self._apply_topology(s["topology"])
        self.selected_node=s["selected"]; self.zoom_level=s["zoom_lvl"]
        self.target_cam_z=s["cam_z"]; self.target_ox=s["ox"]; self.target_oy=s["oy"]
        self.deep_cluster=s.get("dcluster",{})
        self._rebuild_active(); self._compute_hops()
        for n,h in s["hops"].items():
            if n in self.nodes: n.hop=h
        for n,a in s["alphas"].items():
            if n in self.nodes: n.alpha=a
        for n,(tx,ty,tz) in s["targets"].items():
            if n in self.nodes: n.target_x,n.target_y,n.target_z=tx,ty,tz

    def _compute_hops(self):
        for node in self.nodes: node.hop=-1
        if not self.selected_node: return
        idx=self.nodes.index(self.selected_node); q,vis=deque([(idx,0)]),{idx}
        while q:
            cur,d=q.popleft(); self.nodes[cur].hop=d
            if d<3:
                for nb in self.adj[cur]:
                    if nb not in vis: vis.add(nb); q.append((nb,d+1))

    def _update_alphas(self):
        if not self.selected_node:
            for n in self.nodes: n.alpha=1.0; return
        lv=self.zoom_level
        for node in self.nodes:
            h=node.hop
            if   h==0: node.alpha=1.00
            elif h==1: node.alpha=0.95 if lv<2 else 0.65
            elif h==2: node.alpha=0.45 if lv<2 else 0.12
            else:      node.alpha=0.08 if lv<2 else 0.02

    def _rebuild_active(self):
        self.active_edges=set()
        if not self.selected_node: return
        idx=self.nodes.index(self.selected_node)
        for k,(i,j,_) in enumerate(self.edges):
            if i==idx or j==idx: self.active_edges.add(k)

    def update(self, dt, hand_idx, w, h, rot_delta=0.0):
        auto_rot=0.00030 if self.zoom_level>0 else 0.00060
        if self.zoom_level==2:
            self.rotation_y+=auto_rot; self.deep_rot_y+=0.00090+rot_delta; self.deep_rot_x+=0.00015
        else:
            self.rotation_y+=auto_rot+rot_delta; self.deep_rot_y=self.rotation_y; self.deep_rot_x=self.rotation_x
        self.cos_ry=math.cos(self.rotation_y); self.sin_ry=math.sin(self.rotation_y)
        self.cos_rx=math.cos(self.rotation_x); self.sin_rx=math.sin(self.rotation_x)
        self.deep_cos_ry=math.cos(self.deep_rot_y); self.deep_sin_ry=math.sin(self.deep_rot_y)
        self.deep_cos_rx=math.cos(self.deep_rot_x); self.deep_sin_rx=math.sin(self.deep_rot_x)

        spd=0.065
        self.cam_z+=(self.target_cam_z-self.cam_z)*spd
        self.cam_ox+=(self.target_ox-self.cam_ox)*spd
        self.cam_oy+=(self.target_oy-self.cam_oy)*spd

        node_spd=0.038 if self.zoom_level<2 else 0.025
        for node in self.nodes:
            node.x+=(node.target_x-node.x)*node_spd
            node.y+=(node.target_y-node.y)*node_spd
            node.z+=(node.target_z-node.z)*node_spd

        self.zoom_progress+=0.08*(1.0 if self.zoom_level>=1 else -1.0)
        self.zoom_progress=max(0.0,min(1.0,self.zoom_progress))
        self.deep_progress+=0.05*(1.0 if self.zoom_level==2 else -1.0)
        self.deep_progress=max(0.0,min(1.0,self.deep_progress))

        self.hovered_node=None; best_prox=0.0; snap_node=None; snap_dist=80
        if hand_idx:
            px=int(hand_idx[0]*w); py=int(hand_idx[1]*h)
            for node in self.nodes:
                if not node.visible: node.proximity=0.0; continue
                d=math.hypot(node.sx-px,node.sy-py)
                node.proximity=max(0.0,1.0-d/110.0)
                if node.proximity>best_prox and node.proximity>0.30:
                    best_prox=node.proximity; self.hovered_node=node
                if d<snap_dist: snap_dist=d; snap_node=node
        else:
            for node in self.nodes: node.proximity=0.0

        if snap_node and snap_node!=self.selected_node:
            if snap_node==self.dwell_node: self.dwell_timer+=dt
            else: self.dwell_node=snap_node; self.dwell_timer=0.0
        else: self.dwell_node=None; self.dwell_timer=0.0
        return snap_node

    def project(self, cx, cy):
        is_deep=self.zoom_level==2
        for ni,node in enumerate(self.nodes):
            if is_deep and ni in self.deep_cluster:
                lx,ly,lz=self.deep_cluster[ni]
                x=lx*self.deep_cos_ry-lz*self.deep_sin_ry
                z_=lx*self.deep_sin_ry+lz*self.deep_cos_ry
                y=ly; y2=y*self.deep_cos_rx-z_*self.deep_sin_rx; z2=y*self.deep_sin_rx+z_*self.deep_cos_rx
                depth=max(50.0,z2+self.cam_z); sc=380.0/depth
                node.visible=True; node.sx=int(cx+x*sc+self.cam_ox); node.sy=int(cy+y2*sc+self.cam_oy); node.scale=sc
            else:
                x=node.x*self.cos_ry-node.z*self.sin_ry
                z_=node.x*self.sin_ry+node.z*self.cos_ry
                y=node.y; y2=y*self.cos_rx-z_*self.sin_rx; z2=y*self.sin_rx+z_*self.cos_rx
                depth=z2+self.cam_z
                if depth<50: node.visible=False; continue
                node.visible=True; sc=380.0/depth
                node.sx=int(cx+x*sc+self.cam_ox); node.sy=int(cy+y2*sc+self.cam_oy); node.scale=sc

    def find_closest(self, hx, hy, w, h, max_d=130):
        px,py=int(hx*w),int(hy*h); best,bd=None,max_d
        for node in self.nodes:
            if not node.visible: continue
            d=math.hypot(node.sx-px,node.sy-py)
            if d<bd: bd=d; best=node
        return best


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# PARTÃCULAS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class Particle:
    __slots__=['t','speed','ni','nj','rev']
    def __init__(self,i,j):
        self.ni=i; self.nj=j; self.t=random.random()
        self.speed=random.uniform(0.20,0.55); self.rev=random.random()<0.38
    def step(self,dt): self.t=(self.t+self.speed*dt*(-1 if self.rev else 1))%1.0

class ParticleSystem:
    def __init__(self): self.parts=[]
    def rebuild(self,active,edges):
        self.parts=[]
        for k in active:
            if k<len(edges):
                i,j,_=edges[k]; self.parts+=[Particle(i,j),Particle(i,j),Particle(i,j)]
    def step(self,dt):
        for p in self.parts: p.step(dt)
    def draw(self,frame,nodes):
        for p in self.parts:
            n1,n2=nodes[p.ni],nodes[p.nj]
            if not n1.visible or not n2.visible: continue
            t=p.t
            bx=int(n1.sx+(n2.sx-n1.sx)*t); by=int(n1.sy+(n2.sy-n1.sy)*t)
            cv2.circle(frame,(bx,by),5,(0,80,160),-1)   # sem LINE_AA = mais rÃ¡pido
            cv2.circle(frame,(bx,by),3,PART_COL,-1)
            t2=(t-0.07*(-1 if p.rev else 1))%1.0
            bx2=int(n1.sx+(n2.sx-n1.sx)*t2); by2=int(n1.sy+(n2.sy-n1.sy)*t2)
            cv2.circle(frame,(bx2,by2),2,(0,60,120),-1)

class AmbientParticles:
    """VersÃ£o leve: 35 partÃ­culas, atualizaÃ§Ã£o com numpy."""
    def __init__(self, count=35):
        self.count=count
        # Arrays numpy: mais rÃ¡pidos que list of tuples
        self.xs  = np.random.rand(count).astype(np.float32)
        self.ys  = np.random.rand(count).astype(np.float32)
        self.vxs = (np.random.rand(count)-0.5).astype(np.float32)*0.0004
        self.vys = (np.random.rand(count)-0.5).astype(np.float32)*0.0002
        self.rs  = np.random.randint(1,4,(count,)).astype(np.int32)
        self.phs = np.random.rand(count).astype(np.float32)*math.pi*2

    def step(self, dt):
        self.xs=(self.xs+self.vxs)%1.0
        self.ys=(self.ys+self.vys)%1.0
        self.phs+=dt*0.8

    def draw(self, frame, w, h, sidebar_w):
        max_x=w-sidebar_w
        brights=np.clip(18+12*np.sin(self.phs),5,35).astype(np.int32)
        for i in range(self.count):
            px=int(self.xs[i]*max_x); py=int(self.ys[i]*h)
            br=int(brights[i])
            cv2.circle(frame,(px,py),int(self.rs[i]),(br,br*2,br*4),-1)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MINI-GRAPH ENTANGLED â€” cache da shell estÃ¡tica
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class MiniGraphCache:
    """
    Pre-bake da shell Ã¢mbar (parte estÃ¡tica).
    A cada frame sÃ³ composite os nÃ³s/edges dinÃ¢micos em cima.
    """
    def __init__(self, size=75):
        self.size = size
        self.sz   = size * 4
        self.oc   = self.sz // 2
        self.half = size + 24
        self.shell= None   # uint8, serÃ¡ gerado uma vez
        self._build_shell()

    def _build_shell(self):
        sz=self.sz; oc=self.oc; size=self.size
        canvas=np.zeros((sz,sz,3),dtype=np.uint8)

        # NÃ©voa Ã¢mbar exterior
        for r_h in range(size+38, size+5, -4):
            ah=max(0,int((r_h-size-5)/33.0*14))
            cv2.circle(canvas,(oc,oc),r_h,(0,int(ah*0.55),ah),2,cv2.LINE_AA)

        # Shell Ã¢mbar com mÃºltiplas camadas
        shell_tmp=np.zeros((sz,sz,3),dtype=np.uint8)
        for r_off in range(-10,11):
            r=size+r_off
            if r<1: continue
            a=(1.0-abs(r_off)/10.0)**2
            amber=(0,int(a*135),int(a*255))
            cv2.circle(shell_tmp,(oc,oc),r,amber,1,cv2.LINE_AA)
        shell_glow=cv2.GaussianBlur(shell_tmp,(21,21),6)
        cv2.add(canvas,shell_tmp,canvas)
        # Adiciona glow da shell (brightened)
        # Multiplicar por 1.0 nÃ£o muda nada, entÃ£o usamos o glow diretamente
        glow_bright = shell_glow.astype(np.uint8)
        cv2.add(canvas,glow_bright,canvas)

        # MÃ¡scara circular suave
        mask=np.zeros((sz,sz),dtype=np.uint8)
        cv2.circle(mask,(oc,oc),size+24,255,-1)
        for c in range(3): canvas[:,:,c]=cv2.bitwise_and(canvas[:,:,c],mask)

        self.shell=canvas

    def draw(self, frame, net, cx, cy):
        """RÃ¡pido: copia shell cacheada + desenha edges/nodes dinÃ¢micos."""
        sz=self.sz; oc=self.oc; size=self.size; half=self.half

        fy1=cy-half; fy2=cy+half; fx1=cx-half; fx2=cx+half
        if fy1<0 or fx1<0 or fy2>frame.shape[0] or fx2>frame.shape[1]: return

        cy1=oc-half; cy2=oc+half; cx1=oc-half; cx2=oc+half
        if cy1<0 or cx1<0 or cy2>sz or cx2>sz: return

        # Canvas dinÃ¢mico (edges + nodes) em uint8
        dyn=np.zeros((sz,sz,3),dtype=np.uint8)

        # PosiÃ§Ãµes dos nÃ³s com escala fixa (CAM_L0)
        REF_CAM=CAM_L0
        sf=(size*0.80)/(REF_CAM*0.62)
        pts={}
        for ni,node in enumerate(net.nodes):
            x=node.x*net.cos_ry-node.z*net.sin_ry
            z_=node.x*net.sin_ry+node.z*net.cos_ry
            y=node.y; y2=y*net.cos_rx-z_*net.sin_rx; z2=y*net.sin_rx+z_*net.cos_rx
            depth=z2+REF_CAM
            if depth<50: pts[ni]=None; continue
            sc=(380.0*sf)/depth; lx,ly=x*sc,y2*sc
            d=math.hypot(lx,ly); max_r=size*0.78
            if d>max_r and d>0: ratio=max_r/d; lx,ly=lx*ratio,ly*ratio
            pts[ni]=(int(oc+lx),int(oc+ly))

        # Arestas
        for k,(i,j,_) in enumerate(net.edges):
            p1,p2=pts.get(i),pts.get(j)
            if not p1 or not p2: continue
            if math.hypot(p1[0]-oc,p1[1]-oc)>size+6: continue
            if math.hypot(p2[0]-oc,p2[1]-oc)>size+6: continue
            if k in net.active_edges:
                cv2.line(dyn,p1,p2,(12,115,230),1)
            else:
                cv2.line(dyn,p1,p2,(38,46,64),1)

        # NÃ³s com glow simples (2 cÃ­rculos em vez de GaussianBlur)
        for ni,node in enumerate(net.nodes):
            p=pts.get(ni)
            if not p: continue
            if math.hypot(p[0]-oc,p[1]-oc)>size+4: continue
            if node==net.selected_node:
                r=3; col=(20,140,255); glow=(5,40,80)
            elif node.hop==1:
                r=2; col=(25,190,75); glow=(6,50,20)
            else:
                r=2; col=(100,115,150); glow=(25,30,40)
            cv2.circle(dyn,p,r+4,glow,-1)
            cv2.circle(dyn,p,r+2,tuple(c//2 for c in col),-1)
            cv2.circle(dyn,p,r,col,-1)

        # Leve blur nas arestas para cristalino (kernel pequeno = rÃ¡pido)
        dyn_b=cv2.GaussianBlur(dyn,(3,3),0.8)
        cv2.add(dyn,dyn_b,dyn)

        # Composite: shell (cacheada) + dinÃ¢mico â†’ no frame
        crop_shell=self.shell[cy1:cy2,cx1:cx2]
        crop_dyn  =dyn[cy1:cy2,cx1:cx2]
        combined  =cv2.add(crop_shell,crop_dyn)

        # Blit aditivo no frame: cv2.add Ã© mais rÃ¡pido que numpy float
        roi=frame[fy1:fy2,fx1:fx2]
        cv2.add(roi,combined,roi)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# HAND TRACKER
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class HandTracker:

    def __init__(self):
        mp_h=mp.solutions.hands
        self.hands=mp_h.Hands(
            static_image_mode=False, max_num_hands=1,
            min_detection_confidence=0.68, min_tracking_confidence=0.68,
            model_complexity=0,   # â† 0 em vez de 1: muito mais rÃ¡pido
        )
        self.index_tip=None; self.thumb_tip=None
        self._idx_s=None; self._thm_s=None; self._EMA=0.40
        self.pinch_hist=deque(maxlen=6); self.gesture="none"
        self.gesture_hist=deque(maxlen=8); self.prev_ix=None
        self.trail=deque(maxlen=18)   # trail um pouco mais curto
        self._spread_dist_hist=deque(maxlen=8)

    def _ema(self,prev,new):
        if prev is None: return new
        a=self._EMA; return (prev[0]+a*(new[0]-prev[0]),prev[1]+a*(new[1]-prev[1]))

    def process(self,rgb):
        res=self.hands.process(rgb); self.pinch_hist.append(0.0)
        if not res.multi_hand_landmarks or len(res.multi_hand_landmarks) == 0:
            self.gesture_hist.append("none"); self.gesture="none"
            self.prev_ix=None; return

        lm=res.multi_hand_landmarks[0].landmark
        self._idx_s=self._ema(self._idx_s,(lm[8].x,lm[8].y))
        self._thm_s=self._ema(self._thm_s,(lm[4].x,lm[4].y))
        self.index_tip=self._idx_s; self.thumb_tip=self._thm_s

        pd=math.hypot(lm[4].x-lm[8].x,lm[4].y-lm[8].y)
        self.pinch_hist[-1]=max(0.0,1.0-pd*16)
        self._spread_dist_hist.append(pd)

        idx_up=lm[8].y<lm[6].y; mid_up=lm[12].y<lm[10].y
        rng_up=lm[16].y<lm[14].y; pnk_up=lm[20].y<lm[18].y
        three_up=idx_up and mid_up and rng_up

        if   self.get_pinch()>0.58:                  raw_g="pinch"
        elif three_up and not pnk_up:                raw_g="three"
        elif three_up and pnk_up:                    raw_g="four"
        elif idx_up and mid_up and not rng_up:       raw_g="two"
        elif idx_up and not mid_up and not rng_up:   raw_g="point"
        elif not any([idx_up,mid_up,rng_up,pnk_up]): raw_g="fist"
        else:                                        raw_g="open"

        self.gesture_hist.append(raw_g); self.gesture=self._stable()

    def _stable(self):
        if not self.gesture_hist: return "none"
        counts=defaultdict(int)
        for g in self.gesture_hist: counts[g]+=1
        top=max(counts,key=counts.get)
        thr=0.38 if top in ("three","point","four") else 0.50
        return top if counts[top]>=len(self.gesture_hist)*thr else self.gesture

    def get_pinch(self): return sum(self.pinch_hist)/len(self.pinch_hist) if self.pinch_hist else 0.0

    def detect_spread(self):
        if len(self._spread_dist_hist)<6: return False
        hist=list(self._spread_dist_hist); early=sum(hist[:3])/3; late=sum(hist[-3:])/3
        spread=early<0.05 and late>0.14 and (late-early)>0.09
        if spread: self._spread_dist_hist.clear()
        return spread

    def get_rot_delta(self):
        if self.gesture!="two" or self.index_tip is None: self.prev_ix=None; return 0.0
        x=self.index_tip[0]
        if self.prev_ix is None: self.prev_ix=x; return 0.0
        d=(x-self.prev_ix)*8.0; self.prev_ix=x; return d

    def update_trail(self,w,h):
        if self.index_tip:
            self.trail.append((int(self.index_tip[0]*w),int(self.index_tip[1]*h)))

    def draw(self,frame,w,h):
        pts=list(self.trail)
        for i in range(1,len(pts)):
            iv=int(55*(i/len(pts)))
            cv2.line(frame,pts[i-1],pts[i],(iv,iv*2,iv*3),1)  # sem LINE_AA
        for tip,outer,inner in [
            (self.index_tip,(0,55,120),(180,220,255)),
            (self.thumb_tip,(35,35,55),(120,120,155)),
        ]:
            if tip is None: continue
            px,py=int(tip[0]*w),int(tip[1]*h)
            cv2.circle(frame,(px,py),13,outer,-1,cv2.LINE_AA)
            cv2.circle(frame,(px,py),8,inner,-1,cv2.LINE_AA)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# RENDERER
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class Renderer:

    @staticmethod
    def draw_hud_overlay(frame,w,h,sidebar_w,t):
        cx=w-sidebar_w
        # Scan lines: numpy stride em vez de loop cv2.line
        # Aplica escurecimento a linhas alternadas (8px stride)
        # frame[::8,:cx] jÃ¡ estÃ¡ escuro por causa do darken
        arm=28; col_hud=(0,70,148)
        cv2.line(frame,(12,12),(12+arm,12),col_hud,1,cv2.LINE_AA)
        cv2.line(frame,(12,12),(12,12+arm),col_hud,1,cv2.LINE_AA)
        cv2.line(frame,(cx-12,12),(cx-12-arm,12),col_hud,1,cv2.LINE_AA)
        cv2.line(frame,(cx-12,12),(cx-12,12+arm),col_hud,1,cv2.LINE_AA)
        cv2.line(frame,(12,h-12),(12+arm,h-12),col_hud,1,cv2.LINE_AA)
        cv2.line(frame,(12,h-12),(12,h-12-arm),col_hud,1,cv2.LINE_AA)
        cv2.line(frame,(cx-12,h-12),(cx-12-arm,h-12),col_hud,1,cv2.LINE_AA)
        cv2.line(frame,(cx-12,h-12),(cx-12,h-12-arm),col_hud,1,cv2.LINE_AA)
        cv2.putText(frame,"HOLOMIND v19",(20,28),FONT,0.30,(0,70,138),1,cv2.LINE_AA)
        cv2.putText(frame,time.strftime("%H:%M:%S"),(20,42),FONT,0.27,(0,50,95),1,cv2.LINE_AA)

    @staticmethod
    def draw_network(frame,net,parts,snap_node,amb,w,h):
        cx=int((w-SIDEBAR_W)*0.44); cy=int(h*0.47)
        net.project(cx,cy)
        hf,wf=frame.shape[:2]; t=time.time()
        has_sel=net.selected_node is not None; is_deep=net.zoom_level==2
        visible=sorted([n for n in net.nodes if n.visible],key=lambda n:n.scale)

        amb.draw(frame,w,h,SIDEBAR_W)

        # â”€â”€ Arestas (sem LINE_AA para nÃ£o-ativas = mais rÃ¡pido)
        for k,(i,j,lbl) in enumerate(net.edges):
            if i>=len(net.nodes) or j>=len(net.nodes): continue
            n1,n2=net.nodes[i],net.nodes[j]
            if not n1.visible or not n2.visible: continue
            avg_sc=(n1.scale+n2.scale)/2; fade=min(n1.alpha,n2.alpha) if has_sel else 1.0
            is_act=k in net.active_edges
            if is_act:
                al=min(1.0,avg_sc*2.2); w_=2 if is_deep else 1
                cv2.line(frame,(n1.sx,n1.sy),(n2.sx,n2.sy),(0,int(90*al),int(200*al)),w_+1,cv2.LINE_AA)
                cv2.line(frame,(n1.sx,n1.sy),(n2.sx,n2.sy),(0,int(170*al),int(255*al)),w_,cv2.LINE_AA)
                if lbl and is_deep:
                    mx,my=(n1.sx+n2.sx)//2,(n1.sy+n2.sy)//2
                    cv2.putText(frame,lbl,(mx,my),FONT,0.28,(0,80,160),1,cv2.LINE_AA)
            else:
                al=min(1.0,max(0.04,avg_sc*1.2)); iv=int(35*al*fade+5)
                cv2.line(frame,(n1.sx,n1.sy),(n2.sx,n2.sy),(iv//3,iv//2,iv),1)  # sem AA

        # Teia cluster deep-dive
        if is_deep and net.deep_progress>0.1 and net.selected_node:
            sel_idx=net.nodes.index(net.selected_node); cluster=set(net.deep_cluster.keys())
            dp=net.deep_progress; iv_web=int(38*dp)
            for k,(i,j,_) in enumerate(net.edges):
                if i in cluster and j in cluster and i!=sel_idx and j!=sel_idx:
                    ni_,nj_=net.nodes[i],net.nodes[j]
                    if ni_.visible and nj_.visible:
                        cv2.line(frame,(ni_.sx,ni_.sy),(nj_.sx,nj_.sy),(iv_web//2,iv_web,iv_web*2),1)

        parts.draw(frame,net.nodes)

        # â”€â”€ NÃ³s â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for node in visible:
            s=node.scale
            if s<0.07: continue
            is_sel=node==net.selected_node; is_hov=node==net.hovered_node and not is_sel; hop=node.hop

            pulse=1.0+0.06*math.sin(t*2.2+node.pulse_phase)*min(1.0,node.degree/8.0)
            if is_sel and is_deep: pulse=1.0+0.14*math.sin(t*2.5); radius=max(7,int(8.0*s*pulse))
            elif is_sel: pulse=1.0+0.10*math.sin(t*3.0); radius=max(5,int(5.5*s*pulse))
            else: radius=max(3,int(4.0*s*pulse))

            if   is_sel and is_deep: col=NODE_DEEP
            elif is_sel:             col=NODE_SEL
            elif is_hov:             col=NODE_HOV
            elif hop==1:             col=NODE_HOP1
            elif hop==2:             col=NODE_HOP2
            elif node.proximity>0.18: col=NODE_HOV
            else:                    col=NODE_DEF

            fc=lambda c: tuple(max(0,int(v*node.alpha)) for v in c)

            # Glow â€” sÃ³ para nÃ³s importantes (limita nÃºmero de cÃ­rculos)
            if is_sel or is_hov or hop==1:
                glow_r=radius+int(14*(1.0 if is_sel else 0.6))
                gc=tuple(int(v*0.12*node.alpha) for v in col)
                cv2.circle(frame,(node.sx,node.sy),glow_r+4,gc,-1,cv2.LINE_AA)
                gc2=tuple(int(v*0.25*node.alpha) for v in col)
                cv2.circle(frame,(node.sx,node.sy),glow_r,gc2,-1,cv2.LINE_AA)

            cv2.circle(frame,(node.sx,node.sy),radius,fc(col),-1,cv2.LINE_AA)
            if radius>=4:
                hi=tuple(min(255,int(v*1.4)+55) for v in col)
                cv2.circle(frame,(node.sx-radius//3,node.sy-radius//3),max(1,radius//4),hi,-1)

            if is_sel and is_deep and net.deep_progress>0.1:
                dp=net.deep_progress
                for hr_off,am in [(14,0.50),(26,0.28),(42,0.12)]:
                    hr=radius+int(hr_off*dp)
                    hc=tuple(int(v*am*dp) for v in NODE_DEEP)
                    cv2.circle(frame,(node.sx,node.sy),hr,hc,1,cv2.LINE_AA)
                rr=radius+int((48+22*math.sin(t*1.8))*dp)
                cv2.circle(frame,(node.sx,node.sy),max(1,rr),tuple(int(v*0.07*dp) for v in NODE_DEEP),1)

            if is_hov and not is_sel:
                for rr,aa in [(radius+8,0.55),(radius+15,0.22)]:
                    rc=tuple(int(v*aa*node.proximity) for v in NODE_HOV)
                    cv2.circle(frame,(node.sx,node.sy),rr,rc,1,cv2.LINE_AA)

            if hop==1 and has_sel:
                hc=tuple(int(v*0.45*node.alpha) for v in NODE_HOP1)
                cv2.circle(frame,(node.sx,node.sy),radius+5,hc,1,cv2.LINE_AA)

            if node==snap_node and not is_sel and not is_hov:
                cv2.circle(frame,(node.sx,node.sy),radius+10,SNAP_COL,1,cv2.LINE_AA)

            if node==net.dwell_node and net.dwell_timer>0:
                dp2=min(1.0,net.dwell_timer/net.DWELL_TIME); dr=radius+12
                cv2.ellipse(frame,(node.sx,node.sy),(dr,dr),0,-90,-90+int(360*dp2),DWELL_COL,2,cv2.LINE_AA)

            # â”€â”€ Label â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if s>0.22 and node.alpha>0.08:
                if is_sel and is_deep:
                    fs=max(0.48,min(0.92,s*1.0)); txt=f"[ {node.title} ]"; col_l=NODE_DEEP; th=1
                elif is_sel:
                    fs=max(0.35,min(0.62,s*0.72)); txt=f"[ {node.title} ]"; col_l=NODE_SEL; th=1
                elif is_hov or hop==1:
                    fs=max(0.26,min(0.50,s*0.63)); txt=f"[ {node.title} ]"
                    col_l=LBL_HOV if is_hov else LBL_HOP1; th=1
                else:
                    fs=max(0.23,min(0.46,s*0.58)); txt=node.title; col_l=LBL_DEF; th=1
                col_lf=fc(col_l)
                tw=cv2.getTextSize(txt,FONT,fs,th)[0][0]
                cv2.putText(frame,txt,(node.sx-tw//2,node.sy-radius-8),FONT,fs,col_lf,th,cv2.LINE_AA)

            # â”€â”€ Snippets brilhantes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            snip_scale=3.2 if is_sel and is_deep else 1.0
            if s>0.22 and node.alpha>0.06 and node.snippets:
                n_show=min(len(node.snippets),max(1,int(s*2.8)))
                prev_sx2,prev_sy2=None,None
                for ki in range(n_show):
                    base_angle=(ki/max(1,n_show))*math.pi*2+node.pulse_phase
                    z_phase=math.sin(base_angle*1.3+t*0.4)*0.25
                    dist=(48+ki*20)*snip_scale*(1.0+z_phase)
                    sx_=node.sx+int(dist*math.cos(base_angle))
                    sy_=node.sy+int(dist*math.sin(base_angle)*0.8)
                    if 0<sx_<wf and 0<sy_<hf:
                        snip_a=min(1.0,s*1.4)*node.alpha
                        if is_sel and is_deep:
                            sc_=(int(185*snip_a),int(215*snip_a),int(245*snip_a)); fs_snip=0.34
                        elif is_sel:
                            sc_=(int(80*snip_a),int(145*snip_a),int(225*snip_a)); fs_snip=0.27
                        elif hop==1:
                            sc_=(int(60*snip_a),int(185*snip_a),int(90*snip_a)); fs_snip=0.24
                        else:
                            ic=int(135*snip_a); sc_=(ic,ic+10,ic+25); fs_snip=0.22
                        cv2.putText(frame,node.snippets[ki],(sx_,sy_),FONT,fs_snip,sc_,1,cv2.LINE_AA)
                        if is_sel and is_deep and net.deep_progress>0.15:
                            iv_w=int(30*net.deep_progress)
                            cv2.line(frame,(node.sx,node.sy),(sx_,sy_),(iv_w,iv_w*2,iv_w*3),1)
                            if prev_sx2 is not None:
                                iv_r=int(14*net.deep_progress)
                                cv2.line(frame,(prev_sx2,prev_sy2),(sx_,sy_),(iv_r,iv_r,iv_r*2),1)
                        prev_sx2,prev_sy2=sx_,sy_

            if is_sel and net.zoom_progress>0.12:
                Renderer._draw_content(frame,node,net,t)
                Renderer._draw_brackets(frame,node,net,t)

    @staticmethod
    def _draw_content(frame,node,net,t):
        lv=net.zoom_level; al=min(1.0,net.zoom_progress*2.5)
        lines=node.content.split('\n')
        offset_x=260 if lv==2 else 215
        tx=max(16,node.sx-offset_x); ty=node.sy-(len(lines)*26)//2
        sqc=tuple(int(v*al) for v in NODE_SEL)
        cv2.rectangle(frame,(tx,ty-12),(tx+8,ty-4),sqc,-1)
        fs_mult=1.6 if lv==2 else 1.0; line_h=int(30*fs_mult)
        for li,line in enumerate(lines):
            col_line=(tuple(int(v*al) for v in NODE_SEL) if li==0
                      else (int(195*al),int(200*al),int(225*al)))
            cv2.putText(frame,line,(tx+13,ty+li*line_h),FONT,0.44*fs_mult,col_line,1,cv2.LINE_AA)

    @staticmethod
    def _draw_brackets(frame,node,net,t):
        lv=net.zoom_level; prog=net.deep_progress if lv==2 else net.zoom_progress
        b=int(55+32*prog)+int(5*math.sin(t*2.8)); arm=b//3; cx,cy=node.sx,node.sy
        col=tuple(int(v*0.85) for v in NODE_DEEP) if lv==2 else BRACKET
        for corner,p1,p2 in [
            ((cx-b,cy-b),(cx-b+arm,cy-b),(cx-b,cy-b+arm)),
            ((cx+b,cy-b),(cx+b-arm,cy-b),(cx+b,cy-b+arm)),
            ((cx-b,cy+b),(cx-b+arm,cy+b),(cx-b,cy+b-arm)),
            ((cx+b,cy+b),(cx+b-arm,cy+b),(cx+b,cy+b-arm)),
        ]:
            cv2.line(frame,corner,p1,col,1,cv2.LINE_AA); cv2.line(frame,corner,p2,col,1,cv2.LINE_AA)
            cv2.circle(frame,corner,2,col,-1,cv2.LINE_AA)
        cv2.line(frame,(cx-7,cy),(cx+7,cy),(50,60,80),1,cv2.LINE_AA)
        cv2.line(frame,(cx,cy-7),(cx,cy+7),(50,60,80),1,cv2.LINE_AA)
        if lv==2:
            dangle=int(math.degrees(net.deep_rot_y)%360)
            cv2.putText(frame,"DEEP DIVE",(cx-b-55,cy-b+5),FONT,0.28,tuple(int(v*0.65) for v in NODE_DEEP),1,cv2.LINE_AA)
            cv2.putText(frame,f"rot {dangle:03d}Â°",(cx-b-55,cy-b+18),FONT,0.23,(0,60,90),1,cv2.LINE_AA)

    # â”€â”€ Sidebar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @staticmethod
    def draw_sidebar(frame,net,w,h,mini_graph_cache,compose_mode=False,compose_text="",compose_status="",
                     view_mode="mixed",media_hitboxes=None,thumbnail_cache=None,
                     auto_reply_enabled=False,auto_reply_label="",auto_reply_busy=False,
                     notification_text=""):
        sx=w-SIDEBAR_W
        cv2.rectangle(frame,(sx,0),(w,h),(7,7,11),-1)
        cv2.line(frame,(sx,0),(sx,h),(0,50,100),1,cv2.LINE_AA)
        cv2.line(frame,(sx+1,0),(sx+1,h),(0,25,50),1,cv2.LINE_AA)

        def short_lines(text,max_chars=34,max_lines=2):
            words=str(text or "").split()
            if not words: return [""]
            lines=[]; cur=[]
            for wd in words:
                cur.append(wd)
                if len(" ".join(cur))>max_chars:
                    lines.append(" ".join(cur[:-1])); cur=[wd]
                    if len(lines)>=max_lines: break
            if len(lines)<max_lines and cur: lines.append(" ".join(cur))
            lines=lines[:max_lines]
            return [ln if len(ln)<=max_chars else ln[:max_chars-1]+"..." for ln in lines]
        fs=lambda v: v*SIDEBAR_SCALE

        frame[::8,sx+2:w-2]=np.clip(frame[::8,sx+2:w-2].astype(np.int16)+np.array([0,2,3],dtype=np.int16),0,255).astype(np.uint8)

        cv2.putText(frame,"Topologies of",(sx+18,40),FONT,fs(0.80),(195,205,225),1,cv2.LINE_AA)
        cv2.putText(frame,"Thoughts",     (sx+18,70),FONT,fs(0.80),(195,205,225),1,cv2.LINE_AA)
        cv2.line(frame,(sx+18,80),(w-18,80),(0,50,100),1,cv2.LINE_AA)

        mini_graph_cache.draw(frame,net,sx+SIDEBAR_W//2,192)

        lbl,bullets=TOPO_DESC.get(net.topology,(f"mode: {net.topology}",[]))
        my=290
        cv2.putText(frame,lbl,(sx+18,my),FONT,fs(0.37),GRAY_LT,1,cv2.LINE_AA)
        for bi,bul in enumerate(bullets):
            cv2.circle(frame,(sx+22,my+17+bi*18),2,(0,90,160),-1,cv2.LINE_AA)
            cv2.putText(frame,bul,(sx+30,my+21+bi*18),FONT,fs(0.31),(88,108,138),1,cv2.LINE_AA)

        lv_txt=["","L1: focus","L2: deep dive"][net.zoom_level]
        lv_col=NODE_DEEP if net.zoom_level==2 else NODE_SEL
        if lv_txt:
            cv2.putText(frame,lv_txt,(sx+18,my+46),FONT,fs(0.31),lv_col,1,cv2.LINE_AA)
        if net.zoom_level==2:
            cv2.putText(frame,"cluster rotation active",(sx+18,my+60),FONT,fs(0.24),(0,100,140),1,cv2.LINE_AA)

        auto_y=my+74
        if auto_reply_enabled:
            cv2.putText(frame,"auto voice: ON",(sx+18,auto_y),FONT,fs(0.24),(40,190,120),1,cv2.LINE_AA)
            cv2.putText(frame,(auto_reply_label or "chat").strip()[:24],(sx+18,auto_y+13),FONT,fs(0.24),(120,185,210),1,cv2.LINE_AA)
            if auto_reply_busy:
                cv2.putText(frame,"listening...",(sx+18,auto_y+26),FONT,fs(0.23),(0,130,190),1,cv2.LINE_AA)
        else:
            cv2.putText(frame,"auto voice: off (4 fingers)",(sx+18,auto_y),FONT,fs(0.23),(45,62,84),1,cv2.LINE_AA)

        sep=376; cv2.line(frame,(sx+12,sep),(w-12,sep),(0,40,80),1,cv2.LINE_AA)

        if net.selected_node:
            node=net.selected_node; ny=sep+12
            cv2.putText(frame,"focused:",(sx+18,ny+12),FONT,fs(0.27),(0,80,130),1,cv2.LINE_AA)
            title=node.title
            if len(title)>22:
                mid=title.rfind(' ',0,23) or 22
                cv2.putText(frame,title[:mid],(sx+18,ny+30),FONT,fs(0.38),NODE_SEL,1,cv2.LINE_AA)
                cv2.putText(frame,title[mid:].strip(),(sx+18,ny+47),FONT,fs(0.38),NODE_SEL,1,cv2.LINE_AA)
                base_y=ny+63
            else:
                cv2.putText(frame,title,(sx+18,ny+30),FONT,fs(0.38),NODE_SEL,1,cv2.LINE_AA); base_y=ny+46

            neighbors=sum(1 for n in net.nodes if n.hop==1)
            cv2.putText(frame,f"conn: {node.degree}  neighbors: {neighbors}",(sx+18,base_y+2),FONT,fs(0.25),(0,60,100),1,cv2.LINE_AA)

            meta=node.meta if isinstance(node.meta,dict) else {}
            is_chat=(meta.get("type")=="chat")
            info_lines=short_lines(node.info,max_chars=34,max_lines=3 if is_chat else 4)
            for li,line in enumerate(info_lines):
                cv2.putText(frame,line,(sx+18,base_y+18+li*15),FONT,fs(0.28),(100,115,138),1,cv2.LINE_AA)

            if is_chat:
                recent=meta.get("recentMessages",[]) if isinstance(meta.get("recentMessages",[]),list) else []
                cy=base_y+70
                cv2.putText(frame,"recent chat:",(sx+18,cy),FONT,fs(0.28),(0,80,130),1,cv2.LINE_AA)
                y=cy+16
                for msg in recent[-5:]:
                    text=str(msg.get("text","")).strip()
                    if not text: continue
                    sender="you" if msg.get("fromMe") else str(msg.get("sender","contato"))[:10]
                    col=(86,190,120) if msg.get("fromMe") else (130,146,180)
                    media=msg.get("media") if isinstance(msg.get("media"),dict) else None
                    thumb_drawn=False
                    thumb_rect=None
                    thumb_img=None
                    mtype=str(media.get("type")) if media else ""
                    if media and mtype in ("image","video","sticker"):
                        cache_key=None
                        local_path=media.get("localPath")
                        if local_path:
                            cache_key=f"{mtype}:{local_path}"
                        elif media.get("thumbnailB64"):
                            cache_key=f"b64:{media.get('thumbnailB64')[:40]}"

                        if cache_key and isinstance(thumbnail_cache,dict):
                            if cache_key not in thumbnail_cache:
                                loaded=None
                                if local_path and os.path.isfile(local_path):
                                    try:
                                        if mtype=="video":
                                            cap=cv2.VideoCapture(local_path)
                                            ok,frm=cap.read(); cap.release()
                                            loaded=frm if ok else None
                                        else:
                                            raw=cv2.imread(local_path,cv2.IMREAD_UNCHANGED)
                                            if raw is not None and len(raw.shape)==3 and raw.shape[2]==4:
                                                loaded=cv2.cvtColor(raw,cv2.COLOR_BGRA2BGR)
                                            else:
                                                loaded=raw
                                    except Exception:
                                        loaded=None
                                if loaded is None and media.get("thumbnailB64"):
                                    try:
                                        arr=np.frombuffer(base64.b64decode(media.get("thumbnailB64")),dtype=np.uint8)
                                        loaded=cv2.imdecode(arr,cv2.IMREAD_COLOR)
                                    except Exception:
                                        loaded=None
                                thumbnail_cache[cache_key]=loaded
                            thumb_img=thumbnail_cache.get(cache_key)

                        if thumb_img is not None:
                            timg=cv2.resize(thumb_img,(40,40),interpolation=cv2.INTER_AREA)
                            tx1,ty1=w-58,y-11; tx2,ty2=tx1+40,ty1+40
                            if ty2<h-70:
                                frame[ty1:ty2,tx1:tx2]=cv2.addWeighted(frame[ty1:ty2,tx1:tx2],0.20,timg,0.80,0)
                                cv2.rectangle(frame,(tx1,ty1),(tx2,ty2),(0,120,190),1)
                                tag={"image":"IMG","video":"VID","sticker":"STK"}.get(mtype,"MED")
                                cv2.putText(frame,tag,(tx1+2,ty2-3),FONT,0.28,(200,230,255),1,cv2.LINE_AA)
                                thumb_rect=(tx1,ty1,tx2,ty2); thumb_drawn=True

                    line_max=30 if thumb_drawn else 37
                    for ln in short_lines(f"{sender}> {text}",max_chars=line_max,max_lines=2):
                        if y>h-112: break
                        cv2.putText(frame,ln,(sx+18,y),FONT,fs(0.24),col,1,cv2.LINE_AA); y+=13
                    if media and str(media.get("type"))=="audio" and y<=h-112:
                        secs=int(media.get("seconds") or 0)
                        tag=f"[audio {secs}s]" if secs>0 else "[audio]"
                        cv2.putText(frame,tag,(sx+18,y),FONT,fs(0.22),(120,170,210),1,cv2.LINE_AA); y+=12
                    if thumb_drawn and media_hitboxes is not None:
                        media_hitboxes.append({
                            "x1":thumb_rect[0],"y1":thumb_rect[1],"x2":thumb_rect[2],"y2":thumb_rect[3],
                            "image":thumb_img,
                            "media":media,
                            "label":title,
                        })
                    if y>h-112: break
                    y+=2

                if compose_mode:
                    box_y=h-106
                    cv2.rectangle(frame,(sx+14,box_y),(w-14,h-68),(14,18,26),-1)
                    cv2.rectangle(frame,(sx+14,box_y),(w-14,h-68),(0,70,120),1)
                    cv2.putText(frame,"compose [ENTER send | ESC cancel]",(sx+18,box_y+13),FONT,fs(0.25),(0,130,200),1,cv2.LINE_AA)
                    cursor="|" if int(time.time()*2)%2==0 else ""
                    tail=(compose_text or "")[-45:]
                    cv2.putText(frame,tail+cursor,(sx+18,box_y+30),FONT,fs(0.30),(205,215,235),1,cv2.LINE_AA)
                if compose_status:
                    cv2.putText(frame,compose_status[:45],(sx+18,h-56),FONT,fs(0.25),(0,120,170),1,cv2.LINE_AA)
                bar_y=h-42
            else:
                sy=base_y+90
                cv2.putText(frame,"fragments:",(sx+18,sy),FONT,fs(0.26),(0,60,100),1,cv2.LINE_AA)
                for si,snip in enumerate(node.snippets[:6]):
                    cv2.putText(frame,f"  - {snip}",(sx+18,sy+14+si*13),FONT,fs(0.24),(52,68,88),1,cv2.LINE_AA)
                bar_y=sy+98

            bw=SIDEBAR_W-36
            for li,(prog,col_b) in enumerate([(net.zoom_progress,NODE_SEL),(net.deep_progress,NODE_DEEP)]):
                yy=bar_y+li*8
                cv2.rectangle(frame,(sx+18,yy),(sx+18+bw,yy+3),(15,18,26),-1)
                cv2.rectangle(frame,(sx+18,yy),(sx+18+int(bw*prog),yy+3),col_b,-1)
            cv2.putText(frame,"L1",(sx+18+bw+5,bar_y+3),FONT,fs(0.22),(52,62,78),1,cv2.LINE_AA)
            cv2.putText(frame,"L2",(sx+18+bw+5,bar_y+11),FONT,fs(0.22),(52,62,78),1,cv2.LINE_AA)
        else:
            hov=net.hovered_node
            if hov:
                cv2.putText(frame,"hovering:",(sx+18,sep+14),FONT,fs(0.27),(0,70,120),1,cv2.LINE_AA)
                cv2.putText(frame,hov.title[:26],(sx+18,sep+29),FONT,fs(0.35),(100,175,108),1,cv2.LINE_AA)
                if net.dwell_node==hov and net.dwell_timer>0:
                    dp_=net.dwell_timer/net.DWELL_TIME; bw=SIDEBAR_W-36
                    cv2.rectangle(frame,(sx+18,sep+41),(sx+18+bw,sep+43),(16,20,26),-1)
                    cv2.rectangle(frame,(sx+18,sep+41),(sx+18+int(bw*dp_),sep+43),DWELL_COL,-1)
                    cv2.putText(frame,"dwell to select...",(sx+18,sep+53),FONT,fs(0.24),DWELL_COL,1,cv2.LINE_AA)
            else:
                for hi,hint in enumerate(["point to hover","pinch to select","spread = deep dive","3 fingers = back","4 fingers = auto voice"]):
                    cv2.putText(frame,hint,(sx+18,sep+18+hi*15),FONT,fs(0.27),(32,42,58),1,cv2.LINE_AA)

        if net.history:
            cv2.putText(frame,f"3 fingers = back  ({len(net.history)})",(sx+18,h-64),FONT,fs(0.25),(0,80,120),1,cv2.LINE_AA)
        cv2.putText(frame,"4 fingers = auto voice", (sx+18,h-52), FONT, fs(0.25), (0,90,135), 1, cv2.LINE_AA)
        if notification_text:
            cv2.putText(frame,notification_text[:48],(sx+18,h-78),FONT,fs(0.23),(170,210,235),1,cv2.LINE_AA)
        mode_labels=[("1","mixed"),("2","top 10 chats"),("3","groups only")]
        for i,(k,desc) in enumerate(mode_labels):
            is_on=(view_mode=="mixed" and k=="1") or (view_mode=="top10" and k=="2") or (view_mode=="groups" and k=="3")
            col_t=(0,160,255) if is_on else (28,38,52)
            cv2.putText(frame,f"[{k}] {desc}",(sx+18,h-36+i*12),FONT,fs(0.24),col_t,1,cv2.LINE_AA)
    @staticmethod
    def draw_gesture_hint(frame,gesture,w,h,net):
        lv=net.zoom_level
        labels={"point":"â— apontar","two":"âœŒ rotacionar"+(" (cluster)" if lv==2 else ""),
                "three":"ðŸ¤Ÿ  VOLTANDO...","pinch":"â—Ž focar","four":"ðŸ– auto voice","open":"â†” deep dive"}
        txt=labels.get(gesture,""); col=(0,220,80) if gesture in ("three","four") else (42,52,68)
        if txt: cv2.putText(frame,txt,(18,h-18),FONT,0.34,col,1,cv2.LINE_AA)

    @staticmethod
    def draw_fps(frame, fps):
        col=(0,200,80) if fps>=50 else (0,160,255) if fps>=30 else (0,80,200)
        cv2.putText(frame,f"{fps:.0f} fps",(20,h_:=frame.shape[0]-8),FONT,0.32,col,1,cv2.LINE_AA)

    @staticmethod
    def darken(frame):
        # â˜… MAIOR OTIMIZAÃ‡ÃƒO: convertScaleAbs em vez de (frame*0.28).astype(uint8)
        # Economiza ~38ms por frame (era float64 intermediÃ¡rio de 47MB!)
        cv2.convertScaleAbs(frame, frame, alpha=0.28)
        return frame


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# APP
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class HoloMindApp:

    def __init__(self):
        print("â•"*60)
        print("  HoloMind v19 â€” Turbo Edition")
        print("â•"*60)
        print("\n[1/4] CÃ¢mera...")
        self.cap=cv2.VideoCapture(0)
        if not self.cap.isOpened(): self.cap=cv2.VideoCapture(1)
        for p,v in [(cv2.CAP_PROP_FRAME_WIDTH,1920),(cv2.CAP_PROP_FRAME_HEIGHT,1080),
                    (cv2.CAP_PROP_FPS,60),(cv2.CAP_PROP_BUFFERSIZE,1)]:
            self.cap.set(p,v)
        fw=int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)); fh=int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"  {fw}Ã—{fh}")

        print("[2/4] Hand tracker (complexity=0)...")
        self.tracker=HandTracker()

        print("[3/4] Rede...")
        self.net=Network3D(); self.net.build_from_notes(NOTES)
        self.parts=ParticleSystem()

        print("[4/4] Cache mini-graph + partÃ­culas...")
        self.mini_graph_cache=MiniGraphCache(size=75)
        self.ambient=AmbientParticles(35)
        self.memory_bridge=MemoryBridge(MEMORY_API_URL, timeout_s=MEMORY_TIMEOUT_S, action_timeout_s=MEMORY_ACTION_TIMEOUT_S)
        self.memory_sync_interval=max(0.4, MEMORY_SYNC_INTERVAL)
        self.memory_last_attempt=0.0
        self.memory_last_success=0.0
        self.memory_updated_at=None
        self.memory_online=False
        self.latest_notification=""
        self.latest_notification_until=0.0
        self.last_incoming_event_token=""

        self.running=True
        self.fps=60.0; self._fps_acc=0; self._fps_t=time.time()
        self.pinch_was=False; self.undo_was=False; self.undo_cool=0.0; self.dwell_done=False
        self.compose_mode=False
        self.compose_text=""
        self.compose_chat_id=None
        self.compose_status=""
        self.compose_status_until=0.0
        self.compose_send_busy=False
        self.compose_send_result=None
        self.compose_send_lock=threading.Lock()
        self.auto_reply_enabled=False
        self.auto_reply_chat_id=None
        self.auto_reply_chat_label=""
        self.auto_reply_last_ts=0
        self.auto_reply_last_text=""
        self.auto_reply_poll_s=max(0.5, AUTO_REPLY_POLL_S)
        self.auto_reply_last_poll=0.0
        self.auto_reply_busy=False
        self.auto_reply_result=None
        self.auto_reply_lock=threading.Lock()
        self.auto_reply_last_replied_token=""
        self.auto_reply_inflight_token=""
        self.auto_toggle_was=False
        self.auto_toggle_cool=0.0
        self.sidebar_media_hitboxes=[]
        self.thumbnail_cache={}
        self.expanded_media=None
        self.media_video_cap=None
        self.media_video_path=None
        self.latest_notes={}
        self.view_mode="mixed"
        if self._sync_memory_graph(force=True):
            print("  Memory API online. Graph synced.")
        else:
            print("  Memory API offline. Using WhatsApp bootstrap node.")

        print(f"\nâœ“  {len(self.net.nodes)} nÃ³s Â· {len(self.net.edges)} arestas")
        print("\nOtimizaÃ§Ãµes ativas:")
        print("  â€¢ darken: convertScaleAbs (era float64 Ã—47MB â†’ economia ~38ms/frame)")
        print("  â€¢ MediaPipe complexity=0 (~15ms mais rÃ¡pido)")
        print("  â€¢ Mini-graph shell prÃ©-cacheada (blit O(1) por frame)")
        print("  â€¢ PartÃ­culas numpy arrays (35 em vez de 60)")
        print("  â€¢ LINE_AA apenas onde necessÃ¡rio")
        print("\nGestos: â˜ hover Â· ðŸ¤Œ focar Â· â†” deep Â· âœŒ rotacionar Â· ðŸ¤Ÿ voltar Â· ðŸ– auto")
        print("Teclas: 1=mixed 2=top10 3=grupos  D=deep  G=sync  M=summary  T=send  R=reset/sync  U=undo  Q=sair\n")

    def run(self):
        cv2.namedWindow(WIN,cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(WIN,cv2.WND_PROP_FULLSCREEN,cv2.WINDOW_FULLSCREEN)
        cv2.setMouseCallback(WIN, self._on_mouse)
        prev_t=time.time()

        while self.running:
            now=time.time(); dt=min(now-prev_t,0.05); prev_t=now
            self._consume_compose_send_result()
            self._consume_auto_reply_result()
            if self.compose_status and now>=self.compose_status_until:
                self.compose_status=""
            if self.latest_notification and now>=self.latest_notification_until:
                self.latest_notification=""
            if now-self.memory_last_attempt>=self.memory_sync_interval:
                self._sync_memory_graph(force=False)

            ret,frame=self.cap.read()
            if not ret: frame=np.zeros((1080,1920,3),dtype=np.uint8)
            else: frame=cv2.flip(frame,1)
            h,w=frame.shape[:2]

            self.tracker.process(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
            self.tracker.update_trail(w,h)
            gesture=self.tracker.gesture
            rot_delta=self.tracker.get_rot_delta()
            pinch=self.tracker.get_pinch()
            spread=self.tracker.detect_spread()

            if pinch>0.58 and self.tracker.index_tip:
                closest=self.net.find_closest(self.tracker.index_tip[0],self.tracker.index_tip[1],w,h)
                if closest is not None and closest!=self.net.selected_node:
                    self.net.select_node(closest); self.parts.rebuild(self.net.active_edges,self.net.edges)
                    self.dwell_done=False

            if pinch<0.25 and self.pinch_was:
                if self.net.zoom_level==1: self.net._exit_selection(); self.parts.rebuild(set(),self.net.edges)
            self.pinch_was=(pinch>0.58)

            if spread and self.net.selected_node: self.net.deep_dive(); self.net._update_alphas()

            self.undo_cool=max(0.0,self.undo_cool-dt)
            is_undo=(gesture=="three")
            if is_undo and not self.undo_was and self.undo_cool<=0:
                self.net.undo(); self.parts.rebuild(self.net.active_edges,self.net.edges)
                self.undo_cool=0.7
            self.undo_was=is_undo

            self.auto_toggle_cool=max(0.0,self.auto_toggle_cool-dt)
            is_auto_toggle=(gesture=="four")
            if is_auto_toggle and not self.auto_toggle_was and self.auto_toggle_cool<=0:
                self._toggle_auto_reply_for_selected_chat()
                self.auto_toggle_cool=0.9
            self.auto_toggle_was=is_auto_toggle

            snap_node=self.net.update(dt,self.tracker.index_tip,w,h,rot_delta)
            self.ambient.step(dt)
            self._tick_auto_reply(now)

            if self.net.dwell_timer>=self.net.DWELL_TIME and self.net.dwell_node and not self.dwell_done:
                self.net.select_node(self.net.dwell_node)
                self.parts.rebuild(self.net.active_edges,self.net.edges); self.dwell_done=True
            if self.net.dwell_node is None: self.dwell_done=False

            self.parts.step(dt)

            # â”€â”€ Render â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            Renderer.darken(frame)  # in-place, sem criar array novo
            Renderer.draw_hud_overlay(frame,w,h,SIDEBAR_W,now)
            Renderer.draw_network(frame,self.net,self.parts,snap_node,self.ambient,w,h)
            self.sidebar_media_hitboxes=[]
            Renderer.draw_sidebar(frame,self.net,w,h,self.mini_graph_cache,
                                  self.compose_mode,self.compose_text,self.compose_status,
                                  self.view_mode,self.sidebar_media_hitboxes,self.thumbnail_cache,
                                  self.auto_reply_enabled,self.auto_reply_chat_label,self.auto_reply_busy,
                                  self.latest_notification)
            self._draw_media_modal(frame)
            self.tracker.draw(frame,w,h)
            Renderer.draw_gesture_hint(frame,gesture,w,h,self.net)
            Renderer.draw_fps(frame,self.fps)

            cv2.imshow(WIN,frame)
            key=cv2.waitKey(1)&0xFF
            self._key(key)

            # FPS counter
            self._fps_acc+=1
            if now-self._fps_t>=0.5:
                self.fps=self._fps_acc/(now-self._fps_t)
                self._fps_acc=0; self._fps_t=now

        self.cleanup()

    def _key(self,key):
        if self.expanded_media is not None and key==27:
            self._close_media_preview()
            return
        if self.compose_mode:
            self._handle_compose_key(key)
            return

        if key in (ord('q'),27): self.running=False
        elif key==ord('1'):
            self.view_mode="mixed"; self._refresh_view_from_cache()
        elif key==ord('2'):
            self.view_mode="top10"; self._refresh_view_from_cache()
        elif key==ord('3'):
            self.view_mode="groups"; self._refresh_view_from_cache()
        elif key==ord('d'):
            if self.net.selected_node: self.net.deep_dive(); self.net._update_alphas()
        elif key==ord('g'):
            if self._sync_memory_graph(force=True):
                print("[memory] sync ok")
            else:
                print("[memory] sync unchanged/offline")
        elif key==ord('m'):
            self._summarize_selected_chat()
        elif key==ord('t'):
            self._start_compose_for_selected_chat()
        elif key==ord('r'):
            if not self._sync_memory_graph(force=True):
                self.net.build_from_notes(NOTES); self.parts.rebuild(set(),self.net.edges)
        elif key==ord('u'): self.net.undo(); self.parts.rebuild(self.net.active_edges,self.net.edges)

    def _find_node_by_chat_id(self, chat_id):
        if not chat_id:
            return None
        for node in self.net.nodes:
            meta=node.meta if isinstance(node.meta,dict) else {}
            if meta.get("chatId")==chat_id and meta.get("type")=="chat":
                return node
        return None

    def _apply_view_mode(self, notes):
        if not notes:
            return NOTES
        if self.view_mode=="mixed":
            return notes

        items=list(notes.items())
        hub=[(t,d) for (t,d) in items if isinstance(d,dict) and isinstance(d.get("meta"),dict) and d["meta"].get("type")=="hub"]
        chats=[(t,d) for (t,d) in items if isinstance(d,dict) and isinstance(d.get("meta"),dict) and d["meta"].get("type")=="chat"]
        topics=[(t,d) for (t,d) in items if isinstance(d,dict) and isinstance(d.get("meta"),dict) and d["meta"].get("type")=="topic"]

        if self.view_mode=="top10":
            chats=sorted(chats,key=lambda td: (td[1].get("meta",{}).get("lastSeen") or 0),reverse=True)[:10]
        elif self.view_mode=="groups":
            chats=[(t,d) for (t,d) in chats if d.get("meta",{}).get("chatKind")=="grupo"]

        keep_chat_ids={d.get("meta",{}).get("chatId") for (_,d) in chats if d.get("meta",{}).get("chatId")}
        filtered=dict(hub+chats)
        for t,d in topics:
            if d.get("meta",{}).get("chatId") in keep_chat_ids:
                filtered[t]=d
        return filtered if filtered else NOTES

    def _refresh_view_from_cache(self):
        if not self.latest_notes:
            self._sync_memory_graph(force=True)
            return
        selected_chat_id=None
        if self.net.selected_node and isinstance(self.net.selected_node.meta,dict):
            selected_chat_id=self.net.selected_node.meta.get("chatId")
        notes=self._apply_view_mode(self.latest_notes)
        self.net.build_from_notes(notes)
        self.parts.rebuild(set(),self.net.edges)
        if selected_chat_id:
            node=self._find_node_by_chat_id(selected_chat_id)
            if node is not None:
                self.net.select_node(node,push=False)
                self.parts.rebuild(self.net.active_edges,self.net.edges)

    def _sync_memory_graph(self, force=False, preserve_chat_id=None):
        now=time.time()
        if not force and now-self.memory_last_attempt<self.memory_sync_interval:
            return False

        selected_chat_id=preserve_chat_id
        if selected_chat_id is None and self.net.selected_node and isinstance(self.net.selected_node.meta,dict):
            selected_chat_id=self.net.selected_node.meta.get("chatId")

        self.memory_last_attempt=now
        notes,meta=self.memory_bridge.fetch_notes()
        if not notes:
            self.memory_online=False
            return False

        self._update_recent_notification(notes)
        updated_at=(meta or {}).get("updatedAt")
        if not force and self.memory_updated_at and updated_at==self.memory_updated_at:
            self.memory_online=True
            self.memory_last_success=now
            self.latest_notes=notes
            return False

        self.latest_notes=notes
        filtered=self._apply_view_mode(notes)
        self.net.build_from_notes(filtered)
        self.parts.rebuild(set(),self.net.edges)
        if selected_chat_id:
            node=self._find_node_by_chat_id(selected_chat_id)
            if node is not None:
                self.net.select_node(node,push=False)
                self.parts.rebuild(self.net.active_edges,self.net.edges)
        self.memory_updated_at=updated_at
        self.memory_online=True
        self.memory_last_success=now
        print(f"[memory] graph synced: {len(self.net.nodes)} nodes, {len(self.net.edges)} edges")
        return True

    def _summarize_selected_chat(self):
        node=self.net.selected_node
        if not node:
            print("[memory] select a chat node first")
            return
        chat_id=node.meta.get("chatId") if isinstance(node.meta, dict) else None
        if not chat_id:
            print("[memory] selected node has no chatId")
            return
        summary=self.memory_bridge.request_summary(chat_id, send=True)
        if summary:
            print(f"[memory] summary sent to {chat_id}")
        else:
            print("[memory] failed to request summary")

    def _find_chat_note_by_id(self, notes, chat_id):
        if not notes or not chat_id:
            return None, None
        for title,data in notes.items():
            if not isinstance(data,dict):
                continue
            meta=data.get("meta",{}) if isinstance(data.get("meta",{}),dict) else {}
            if meta.get("type")=="chat" and meta.get("chatId")==chat_id:
                return title, meta
        return None, None

    def _latest_incoming_from_meta(self, meta):
        if not isinstance(meta,dict):
            return None
        recent=meta.get("recentMessages",[])
        if not isinstance(recent,list):
            return None
        best=None
        for msg in recent:
            if not isinstance(msg,dict):
                continue
            if bool(msg.get("fromMe")):
                continue
            text=str(msg.get("text","")).strip()
            if not text:
                continue
            ts=int(msg.get("timestamp") or 0)
            if best is None or ts>=best["timestamp"]:
                best={"timestamp":ts,"text":text,"sender":str(msg.get("sender","contato"))}
        return best

    def _latest_message_from_meta(self, meta):
        if not isinstance(meta,dict):
            return None
        recent=meta.get("recentMessages",[])
        if not isinstance(recent,list):
            return None
        best=None
        for idx,msg in enumerate(recent):
            if not isinstance(msg,dict):
                continue
            text=str(msg.get("text","")).strip()
            ts=int(msg.get("timestamp") or 0)
            cand={
                "timestamp": ts,
                "text": text,
                "sender": str(msg.get("sender","contato")),
                "fromMe": bool(msg.get("fromMe")),
                "_idx": idx,
            }
            if best is None:
                best=cand
                continue
            if ts>best["timestamp"] or (ts==best["timestamp"] and idx>=best["_idx"]):
                best=cand
        return best

    def _message_event_token(self, chat_id, msg):
        if not chat_id or not isinstance(msg,dict):
            return ""
        ts=int(msg.get("timestamp") or 0)
        sender=str(msg.get("sender","")).strip().lower()
        text=str(msg.get("text","")).strip().lower()
        return f"{chat_id}|{ts}|{sender}|{text}"

    def _update_recent_notification(self, notes):
        if not notes:
            return
        best=None
        best_title=""
        best_chat_id=""
        for title,data in notes.items():
            if not isinstance(data,dict):
                continue
            meta=data.get("meta",{}) if isinstance(data.get("meta",{}),dict) else {}
            if meta.get("type")!="chat":
                continue
            msg=self._latest_message_from_meta(meta)
            if not msg or msg.get("fromMe"):
                continue
            if best is None or int(msg.get("timestamp") or 0)>int(best.get("timestamp") or 0):
                best=msg
                best_title=title
                best_chat_id=str(meta.get("chatId") or "")
        if not best:
            return

        token=self._message_event_token(best_chat_id,best)
        if not token:
            return
        if not self.last_incoming_event_token:
            self.last_incoming_event_token=token
            return
        if token==self.last_incoming_event_token:
            return

        self.last_incoming_event_token=token
        txt=best.get("text") or "[mensagem]"
        self.latest_notification=f"new {best_title[:16]}: {str(txt)[:30]}"
        self.latest_notification_until=time.time()+6.0

    def _compact_auto_reply_text(self, text):
        clean=re.sub(r"\s+"," ",str(text or "")).strip()
        if not clean:
            return "Beleza, te respondo em seguida."
        parts=re.split(r"(?<=[.!?])\s+",clean)
        if len(parts)>2:
            clean=" ".join(parts[:2]).strip()
        limit=max(80,AUTO_REPLY_MAX_CHARS)
        if len(clean)>limit:
            cut=clean[:limit]
            sp=cut.rfind(" ")
            clean=(cut[:sp] if sp>42 else cut).rstrip(" ,;:-") + "..."
        return clean

    def _prime_auto_reply_cursor(self, chat_id):
        notes=self.latest_notes
        if not notes:
            notes,_=self.memory_bridge.fetch_notes()
        if not notes:
            return None
        title,meta=self._find_chat_note_by_id(notes,chat_id)
        latest_any=self._latest_message_from_meta(meta)
        incoming=self._latest_incoming_from_meta(meta)
        with self.auto_reply_lock:
            self.auto_reply_last_replied_token=self._message_event_token(chat_id, latest_any)
            self.auto_reply_inflight_token=""
            if incoming:
                self.auto_reply_last_ts=incoming["timestamp"]
                self.auto_reply_last_text=incoming["text"]
            else:
                self.auto_reply_last_ts=0
                self.auto_reply_last_text=""
        return title

    def _toggle_auto_reply_for_selected_chat(self):
        if self.auto_reply_enabled:
            label=self.auto_reply_chat_label or "chat"
            self.auto_reply_enabled=False
            self.auto_reply_chat_id=None
            self.auto_reply_chat_label=""
            with self.auto_reply_lock:
                self.auto_reply_inflight_token=""
            self._set_compose_status(f"auto voice off: {label[:18]}",duration=2.8)
            print(f"[auto] disabled ({label})")
            return

        node=self.net.selected_node
        if not node:
            self._set_compose_status("select a chat for auto voice",duration=3.0)
            return
        meta=node.meta if isinstance(node.meta,dict) else {}
        chat_id=meta.get("chatId")
        if not chat_id or meta.get("type")!="chat":
            self._set_compose_status("selected node is not a chat",duration=3.0)
            return

        label=self._prime_auto_reply_cursor(chat_id) or node.title
        self.auto_reply_enabled=True
        self.auto_reply_chat_id=chat_id
        self.auto_reply_chat_label=label
        self.auto_reply_last_poll=0.0
        self._set_compose_status(f"auto voice on: {label[:18]}",duration=3.4)
        print(f"[auto] enabled for {chat_id} ({label})")

    def _tick_auto_reply(self, now):
        if not self.auto_reply_enabled or not self.auto_reply_chat_id:
            return
        if now-self.auto_reply_last_poll<self.auto_reply_poll_s:
            return
        with self.auto_reply_lock:
            if self.auto_reply_busy:
                return
            self.auto_reply_busy=True
            self.auto_reply_last_poll=now
        threading.Thread(target=self._auto_reply_worker,args=(self.auto_reply_chat_id,),daemon=True).start()

    def _auto_reply_worker(self, chat_id):
        result=None
        try:
            if not self.auto_reply_enabled or chat_id!=self.auto_reply_chat_id:
                return
            notes,_=self.memory_bridge.fetch_notes()
            if not notes:
                return

            _title,meta=self._find_chat_note_by_id(notes,chat_id)
            latest_any=self._latest_message_from_meta(meta)
            if not latest_any:
                return
            if latest_any.get("fromMe"):
                return
            incoming=self._latest_incoming_from_meta(meta)
            if not incoming:
                return
            target_token=self._message_event_token(chat_id,latest_any)
            if not target_token:
                return

            with self.auto_reply_lock:
                if target_token==self.auto_reply_last_replied_token:
                    return
                if target_token==self.auto_reply_inflight_token:
                    return
                self.auto_reply_inflight_token=target_token
                old_ts=self.auto_reply_last_ts
                old_text=self.auto_reply_last_text
            new_msg=(
                incoming["timestamp"]>old_ts
                or (incoming["timestamp"]==old_ts and incoming["text"]!=old_text)
            )
            if not new_msg:
                return

            suggestion=self.memory_bridge.request_reply_suggestion(chat_id,incoming["text"])
            if not suggestion:
                result=(chat_id,False,"failed to get reply suggestion",False)
                return
            if not self.auto_reply_enabled or chat_id!=self.auto_reply_chat_id:
                return

            reply=self._compact_auto_reply_text(suggestion)
            notes2,_=self.memory_bridge.fetch_notes()
            if not notes2:
                return
            _title2,meta2=self._find_chat_note_by_id(notes2,chat_id)
            latest_before_send=self._latest_message_from_meta(meta2)
            latest_token_before_send=self._message_event_token(chat_id,latest_before_send)
            if not latest_before_send or latest_before_send.get("fromMe") or latest_token_before_send!=target_token:
                return

            prefer_audio=(random.random()<0.5)
            if prefer_audio:
                ok,detail=self.memory_bridge.send_message_result(
                    chat_id,
                    reply,
                    as_audio=True,
                    profile_id=resolve_voice_profile_id(VOICE_PROFILE_ID),
                    language=VOICE_LANGUAGE,
                    fallback_to_text=False,
                )
                if not ok:
                    ok2,detail2=self.memory_bridge.send_message_result(
                        chat_id,
                        reply,
                        as_audio=True,
                        profile_id=resolve_voice_profile_id(VOICE_PROFILE_ID),
                        language=VOICE_LANGUAGE,
                        fallback_to_text=False,
                    )
                    if ok2:
                        ok,detail=True,f"audio retry ok ({detail2})"
                    else:
                        ok,detail=self.memory_bridge.send_message_result(
                            chat_id,
                            reply,
                            as_audio=False,
                            profile_id=resolve_voice_profile_id(VOICE_PROFILE_ID),
                            language=VOICE_LANGUAGE,
                            fallback_to_text=False,
                        )
                        if ok:
                            detail=f"audio failed twice -> text sent ({detail})"
                        else:
                            detail=f"audio failed twice ({detail2}) | text failed ({detail})"
            else:
                ok,detail=self.memory_bridge.send_message_result(
                    chat_id,
                    reply,
                    as_audio=False,
                    profile_id=resolve_voice_profile_id(VOICE_PROFILE_ID),
                    language=VOICE_LANGUAGE,
                    fallback_to_text=False,
                )

            if ok:
                with self.auto_reply_lock:
                    self.auto_reply_last_ts=incoming["timestamp"]
                    self.auto_reply_last_text=incoming["text"]
                    self.auto_reply_last_replied_token=target_token
            result=(chat_id,ok,detail,True)
        finally:
            with self.auto_reply_lock:
                self.auto_reply_inflight_token=""
                self.auto_reply_busy=False
                if result is not None:
                    self.auto_reply_result=result

    def _consume_auto_reply_result(self):
        with self.auto_reply_lock:
            result=self.auto_reply_result
            self.auto_reply_result=None
        if result is None:
            return
        chat_id,ok,detail,attempted_send=result
        if attempted_send and ok:
            print(f"[auto] reply sent to {chat_id} ({detail})")
            self._set_compose_status("auto voice reply sent",duration=3.0)
            self._sync_memory_graph(force=True,preserve_chat_id=chat_id)
        elif attempted_send:
            print(f"[auto] failed for {chat_id}: {detail}")
            self._set_compose_status(f"auto failed: {detail}",duration=3.8)

    def _set_compose_status(self, msg, duration=2.8):
        self.compose_status=msg
        self.compose_status_until=time.time()+duration

    def _start_compose_for_selected_chat(self):
        if self.compose_send_busy:
            self._set_compose_status("send in progress")
            return
        node=self.net.selected_node
        if not node:
            print("[memory] select a chat node first")
            self._set_compose_status("select a chat node first")
            return
        chat_id=node.meta.get("chatId") if isinstance(node.meta, dict) else None
        if not chat_id:
            print("[memory] selected node has no chatId")
            self._set_compose_status("selected node has no chatId")
            return

        self.compose_mode=True
        self.compose_chat_id=chat_id
        self.compose_text=""
        self._set_compose_status(f"compose -> {node.title[:20]}", duration=2.0)

    def _handle_compose_key(self,key):
        if key in (255,):
            return

        if key in (27,):  # ESC
            self.compose_mode=False
            self.compose_text=""
            self.compose_chat_id=None
            self._set_compose_status("compose canceled")
            return

        if key in (13,10):  # ENTER
            self._submit_compose_message()
            return

        if key in (8,127):  # BACKSPACE
            self.compose_text=self.compose_text[:-1]
            return

        if key==32 or 33<=key<=126:
            if len(self.compose_text)<320:
                self.compose_text+=chr(key)

    def _submit_compose_message(self):
        if self.compose_send_busy:
            self._set_compose_status("send in progress")
            return

        chat_id=self.compose_chat_id
        text=self.compose_text.strip()
        if not chat_id:
            self.compose_mode=False
            self._set_compose_status("compose target missing")
            return
        if not text:
            self._set_compose_status("type a message first")
            return

        self.compose_mode=False
        self.compose_text=""
        self.compose_chat_id=None
        self.compose_send_busy=True
        self._set_compose_status("sending audio...", duration=20.0)
        threading.Thread(
            target=self._compose_send_worker,
            args=(chat_id, text),
            daemon=True,
        ).start()

    def _compose_send_worker(self, chat_id, text):
        try:
            ok, detail = self.memory_bridge.send_message_result(
                chat_id,
                text,
                as_audio=True,
                profile_id=resolve_voice_profile_id(VOICE_PROFILE_ID),
                language=VOICE_LANGUAGE,
                fallback_to_text=(not VOICE_STRICT_MODE),
            )
        except Exception as error:
            ok, detail = False, f"send worker error: {error}"

        with self.compose_send_lock:
            self.compose_send_result = (chat_id, bool(ok), str(detail))
            self.compose_send_busy = False

    def _consume_compose_send_result(self):
        with self.compose_send_lock:
            result = self.compose_send_result
            self.compose_send_result = None
        if result is None:
            return

        chat_id, ok, detail = result
        if ok:
            print(f"[memory] message sent to {chat_id} ({detail})")
            if "fallback" in detail.lower():
                self._set_compose_status("voice offline -> sent as text", duration=4.2)
            else:
                self._set_compose_status("message sent")
            self._sync_memory_graph(force=True,preserve_chat_id=chat_id)
        else:
            print(f"[memory] failed to send message: {detail}")
            if VOICE_STRICT_MODE:
                self._set_compose_status(f"voice failed: {detail}", duration=5.2)
            else:
                self._set_compose_status(f"send failed: {detail}", duration=4.8)

    def _on_mouse(self,event,x,y,_flags,_param):
        if event!=cv2.EVENT_LBUTTONDOWN:
            return
        if self.expanded_media is not None:
            self._close_media_preview()
            return
        for hb in self.sidebar_media_hitboxes:
            if hb["x1"]<=x<=hb["x2"] and hb["y1"]<=y<=hb["y2"]:
                self._open_media_preview(hb.get("media"),hb.get("label","media"),hb.get("image"))
                break

    def _close_media_preview(self):
        self.expanded_media=None
        if self.media_video_cap is not None:
            self.media_video_cap.release()
        self.media_video_cap=None
        self.media_video_path=None

    def _open_media_preview(self,media,label,fallback_img=None):
        media=media if isinstance(media,dict) else {}
        mtype=str(media.get("type",""))
        local_path=media.get("localPath")

        if mtype=="video" and local_path and os.path.isfile(local_path):
            self.expanded_media={"type":"video","path":local_path,"label":label}
            return

        if mtype in ("image","sticker") and local_path and os.path.isfile(local_path):
            raw=cv2.imread(local_path,cv2.IMREAD_UNCHANGED)
            if raw is not None:
                img=cv2.cvtColor(raw,cv2.COLOR_BGRA2BGR) if len(raw.shape)==3 and raw.shape[2]==4 else raw
                self.expanded_media={"type":mtype,"image":img,"label":label}
                return

        if fallback_img is not None:
            self.expanded_media={"type":mtype or "image","image":fallback_img,"label":label}
            return

        self._set_compose_status("media file not available yet")

    def _next_video_frame(self,path):
        if not path or not os.path.isfile(path):
            return None
        if self.media_video_path!=path or self.media_video_cap is None:
            if self.media_video_cap is not None:
                self.media_video_cap.release()
            self.media_video_cap=cv2.VideoCapture(path)
            self.media_video_path=path
        if self.media_video_cap is None or not self.media_video_cap.isOpened():
            return None
        ok,frm=self.media_video_cap.read()
        if not ok:
            self.media_video_cap.set(cv2.CAP_PROP_POS_FRAMES,0)
            ok,frm=self.media_video_cap.read()
        return frm if ok else None

    def _draw_media_modal(self,frame):
        if self.expanded_media is None:
            return
        kind=str(self.expanded_media.get("type","image"))
        if kind=="video":
            img=self._next_video_frame(self.expanded_media.get("path"))
        else:
            img=self.expanded_media.get("image")
        if img is None:
            return
        h,w=frame.shape[:2]
        overlay=frame.copy()
        cv2.rectangle(overlay,(0,0),(w,h),(2,6,12),-1)
        cv2.addWeighted(overlay,0.72,frame,0.28,0,frame)

        max_w=int(w*0.62); max_h=int(h*0.70)
        ih,iw=img.shape[:2]
        scale=min(max_w/max(1,iw),max_h/max(1,ih))
        nw,nh=max(1,int(iw*scale)),max(1,int(ih*scale))
        view=cv2.resize(img,(nw,nh),interpolation=cv2.INTER_LINEAR)
        x1=(w-nw)//2; y1=(h-nh)//2; x2=x1+nw; y2=y1+nh
        frame[y1:y2,x1:x2]=view
        cv2.rectangle(frame,(x1-10,y1-10),(x2+10,y2+10),(0,120,200),1,cv2.LINE_AA)
        label=str(self.expanded_media.get("label","media"))[:36]
        cv2.putText(frame,f"{kind} preview: {label}",(x1-8,y1-18),FONT,0.44,(170,220,250),1,cv2.LINE_AA)
        cv2.putText(frame,"click or ESC to close",(x1-8,y2+24),FONT,0.33,(120,175,220),1,cv2.LINE_AA)

    def cleanup(self):
        self._close_media_preview()
        print("\nEncerrando..."); self.cap.release(); cv2.destroyAllWindows(); print("âœ“ OK")


if __name__=="__main__":
    app=HoloMindApp(); app.run()

