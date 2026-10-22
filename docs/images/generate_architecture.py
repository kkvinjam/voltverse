"""Regenerate the architecture diagram:  python3 docs/images/generate_architecture.py docs/images/voltverse-architecture.svg
(PNG: rsvg-convert -w 1720 docs/images/voltverse-architecture.svg -o docs/images/voltverse-architecture.png)"""
import html
import sys

W, H = 1720, 1180
out = []
A = out.append

C = {
    "net": "#8C4FFF", "compute": "#ED7100", "db": "#C925D1", "ai": "#01A88D", "mgmt": "#E7157B",
    "storage": "#7AA116", "sec": "#DD344C", "ink": "#232F3E", "muted": "#5f6b7a",
    "req": "#2563EB", "agent": "#7C3AED", "tel": "#E8590C", "deploy": "#6B7280",
}


def esc(s):
    return html.escape(s, quote=True)


def group(x, y, w, h, label, stroke, fill="none", dash=None, icon=None, label_color=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    A(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.6"{d}/>')
    tx = x + 10
    if icon:
        A(f'<rect x="{x}" y="{y}" width="26" height="26" fill="{stroke}"/>')
        A(f'<text x="{x+13}" y="{y+18}" font-size="11" font-weight="700" fill="#fff" text-anchor="middle">{esc(icon)}</text>')
        tx = x + 34
    A(f'<text x="{tx}" y="{y+18}" font-size="13" font-weight="600" fill="{label_color or stroke}">{esc(label)}</text>')


def node(x, y, w, h, title, sub=None, color=C["compute"], badge=None, sub2=None, bold=False):
    A(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#ffffff" stroke="{color}" stroke-width="{2.4 if bold else 1.6}" filter="url(#sh)"/>')
    A(f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{color}"/>')
    cx = x + 14
    if badge:
        A(f'<rect x="{x+12}" y="{y+(h-30)/2}" width="30" height="30" rx="6" fill="{color}"/>')
        A(f'<text x="{x+27}" y="{y+(h-30)/2+20}" font-size="12" font-weight="700" fill="#fff" text-anchor="middle">{esc(badge)}</text>')
        cx = x + 50
    lines = [t for t in (title, sub, sub2) if t]
    top = y + h / 2 - (len(lines) - 1) * 8 + 4
    for i, t in enumerate(lines):
        size, weight, fill = (13.5, 700, C["ink"]) if i == 0 else (11, 400, C["muted"])
        A(f'<text x="{cx}" y="{top + i*16}" font-size="{size}" font-weight="{weight}" fill="{fill}">{esc(t)}</text>')


def arrow(points, color, label=None, lx=None, ly=None, dash=None, width=2, anchor="middle", both=False):
    d = "M " + " L ".join(f"{px} {py}" for px, py in points)
    da = f' stroke-dasharray="{dash}"' if dash else ""
    mk = color.strip("#")
    start = f' marker-start="url(#s{mk})"' if both else ""
    A(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{da} marker-end="url(#a{mk})"{start}/>')
    if label:
        A(f'<text x="{lx}" y="{ly}" font-size="11" font-weight="600" fill="{color}" text-anchor="{anchor}" '
          f'paint-order="stroke" stroke="#ffffff" stroke-width="4">{esc(label)}</text>')


A(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
  f'font-family="Inter, -apple-system, Segoe UI, Helvetica, Arial, sans-serif">')
A('<defs><filter id="sh" x="-5%" y="-5%" width="110%" height="120%"><feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-opacity="0.12"/></filter>')
for col in (C["req"], C["agent"], C["tel"], C["deploy"], C["db"], C["ai"]):
    k = col.strip("#")
    A(f'<marker id="a{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{col}"/></marker>')
    A(f'<marker id="s{k}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{col}"/></marker>')
A('</defs>')
A(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

# Title
A(f'<text x="30" y="42" font-size="24" font-weight="800" fill="{C["ink"]}">⚡ VoltVerse — architecture</text>')
A(f'<text x="30" y="66" font-size="13" fill="{C["muted"]}">Microservices e-commerce on Amazon EKS Auto Mode · Sparky AI agent (Strands Agents + Amazon Bedrock) · all telemetry to Amazon CloudWatch via OpenTelemetry</text>')

# Users
node(28, 360, 130, 74, "Shoppers", "browser", color=C["ink"], badge="👤")

# AWS cloud / region / VPC
group(178, 88, 1520, 1072, "AWS Cloud", C["ink"], icon="aws", label_color=C["ink"])
node(196, 340, 180, 114, "Amazon CloudFront", "HTTPS · *.cloudfront.net", color=C["net"], badge="CF", sub2="/assets cached")
group(388, 112, 1294, 1032, "Region (e.g. us-east-1)", "#147EBA", dash="6 4", icon="R")
group(408, 146, 912, 794, "VPC 10.42.0.0/16 · 2 AZs", "#8C4FFF", icon="VPC")

# public subnet
group(428, 176, 872, 58, "Public subnets", "#7AA116", fill="#F2F8E9")
node(600, 182, 170, 44, "NAT gateway", color=C["net"])
node(800, 182, 170, 44, "Internet gateway", color=C["net"])

# private subnet
group(428, 248, 872, 676, "Private subnets", "#147EBA", fill="#EEF6FC")
node(438, 356, 178, 92, "Internal ALB", "HTTP :80 · no public IP", color=C["net"], badge="ALB", sub2="SG: CloudFront only")
node(438, 466, 178, 44, "AWS WAF", "rate caps · allow-list", color=C["sec"])
node(438, 764, 178, 80, "CodeBuild deployer", "build · push · kubectl", color=C["deploy"], badge="CB")

# EKS cluster
group(620, 262, 666, 648, "Amazon EKS — Auto Mode (managed nodes, load balancing, Pod Identity)", C["compute"], fill="#FFF8F1", icon="EKS")
group(636, 296, 478, 600, "namespace: voltverse", C["compute"], fill="#ffffff", dash="4 3")
node(652, 336, 170, 84, "storefront", "React SPA + nginx", color=C["compute"], badge="UI", sub2="API gateway (allow-list)", bold=True)
svc = [("catalog", "products · search"), ("inventory", "stock · reservations"), ("cart", "anonymous carts"),
       ("checkout", "saga: stock → pay → order"), ("payment", "simulated processor"), ("orders", "tracking · cancel · returns")]
for i, (n, sub) in enumerate(svc):
    node(900, 322 + i * 62, 198, 50, n, sub, color=C["compute"], bold=(n == "checkout"))
node(652, 520, 196, 128, "Sparky (assistant)", "Strands Agents · 13 tools", color=C["ai"], badge="AI",
     sub2="SSE streaming · ADOT SDK", bold=True)
node(652, 800, 170, 56, "loadgen", "synthetic shoppers", color=C["deploy"])

group(1128, 296, 144, 220, "ns: observability", C["tel"], fill="#ffffff", dash="4 3")
node(1138, 334, 124, 112, "ADOT Collector", "OTLP receiver", color=C["tel"], sub2="→ CloudWatch")
node(1128, 540, 144, 54, "EKS Pod Identity", "per-service IAM roles", color=C["sec"])
node(1128, 608, 144, 54, "Auto Mode nodes", "EC2, x86_64", color=C["compute"])

# Regional services
node(1346, 150, 316, 92, "Amazon Bedrock", "Claude Opus 5.5 · global inference profile", color=C["ai"], badge="BR", sub2="Converse / ConverseStream")
node(1346, 270, 316, 132, "Amazon DynamoDB", "products · inventory · carts", color=C["db"], badge="DDB", sub2="orders · payments (GSI orderId)")
A(f'<text x="1360" y="392" font-size="10.5" fill="{C["muted"]}">via VPC gateway endpoint · on-demand · encrypted</text>')

group(1346, 428, 316, 330, "Amazon CloudWatch", C["mgmt"], fill="#FDF2F8", icon="CW")
cw = [("Transaction Search", "spans → aws/spans · X-Ray trace map"),
      ("GenAI Observability", "Bedrock AgentCore view: Sparky"),
      ("CloudWatch Logs", "app logs · agent log group"),
      ("CloudWatch Metrics", "VoltVerse namespace (EMF)"),
      ("Dashboard", "business + service + agent health")]
for i, (t, s) in enumerate(cw):
    node(1360, 462 + i * 58, 288, 48, t, s, color=C["mgmt"])

node(1346, 782, 150, 56, "Amazon ECR", "8 repositories", color=C["compute"])
node(1512, 782, 150, 56, "Amazon S3", "source zip (opt.)", color=C["storage"])

# deployment row
group(408, 960, 912, 168, "Provisioning — one CloudFormation template per account", C["deploy"], dash="6 4")
node(428, 1000, 190, 84, "AWS CloudFormation", "VPC · EKS · DDB · ALB", color=C["mgmt"], badge="CFN", sub2="CloudFront · WAF · IAM")
node(668, 1000, 190, 84, "Custom resource", "AWS Lambda", color=C["compute"], badge="λ", sub2="starts the build")
node(908, 1000, 190, 84, "CodeBuild deployer", "images · seed data", color=C["deploy"], badge="CB", sub2="kubectl apply · callback")
node(1148, 1000, 150, 84, "GitHub / S3", "application source", color=C["storage"])
arrow([(618, 1042), (666, 1042)], C["deploy"])
arrow([(858, 1042), (906, 1042)], C["deploy"])
arrow([(1146, 1042), (1100, 1042)], C["deploy"], "clone", 1123, 1034)
arrow([(1003, 1000), (1003, 960), (527, 960), (527, 846)], C["deploy"], "runs in VPC", 760, 954, dash="5 4")

# ---------- request path (blue)
arrow([(158, 397), (198, 397)], C["req"], "HTTPS", 178, 388)
arrow([(376, 400), (436, 400)], C["req"], "VPC origin", 406, 391)
arrow([(616, 378), (650, 378)], C["req"], "TGB", 633, 370)
# gateway bus to microservices
arrow([(822, 362), (866, 362)], C["req"], width=2)
A(f'<path d="M 866 347 V 657" stroke="{C["req"]}" stroke-width="2" fill="none"/>')
for i in range(6):
    y = 347 + i * 62
    arrow([(866, y), (898, y)], C["req"], width=2)
A(f'<text x="836" y="330" font-size="11" font-weight="600" fill="{C["req"]}" text-anchor="middle">REST /api/*</text>')
arrow([(737, 420), (737, 518)], C["req"], "/api/assistant (SSE)", 742, 474, anchor="start")
# checkout saga
# services -> DynamoDB
arrow([(1098, 300), (1110, 300), (1110, 284), (1344, 284)], C["db"], "DynamoDB API", 1230, 278, width=2)
A(f'<path d="M 1098 347 H 1110 M 1098 409 H 1110 M 1098 471 H 1110 M 1098 595 H 1110 M 1098 657 H 1110 M 1110 300 V 657" stroke="{C["db"]}" stroke-width="2" fill="none"/>')

# ---------- agent (purple / teal)
arrow([(848, 560), (884, 560), (884, 371), (898, 371)], C["agent"], width=2)
for y in (433, 495, 557, 619, 681):
    arrow([(884, y - 10), (898, y - 10)], C["agent"], width=2)
A(f'<path d="M 884 371 V 671" stroke="{C["agent"]}" stroke-width="2" fill="none"/>')
A(f'<text x="892" y="702" font-size="11" font-weight="600" fill="{C["agent"]}" text-anchor="start">Sparky tool calls (HTTP)</text>')
arrow([(652, 545), (628, 545), (628, 254), (1316, 254), (1316, 196), (1344, 196)], C["ai"], "Bedrock Converse (SigV4, Pod Identity)", 1080, 250, width=2)

# ---------- telemetry (orange dashed)
arrow([(1114, 400), (1136, 400)], C["tel"], dash="5 4", width=2)
A(f'<text x="1120" y="392" font-size="10.5" font-weight="600" fill="{C["tel"]}" text-anchor="middle">OTLP</text>')
arrow([(1262, 420), (1300, 420), (1300, 486), (1358, 486)], C["tel"], dash="5 4", width=2)
arrow([(1300, 486), (1300, 600), (1358, 600)], C["tel"], dash="5 4", width=2)
arrow([(1300, 600), (1300, 658), (1358, 658)], C["tel"], "SigV4 OTLP + EMF", 1294, 742, dash="5 4", width=2, anchor="end")
arrow([(836, 648), (836, 884), (1310, 884), (1310, 544), (1358, 544)], C["tel"],
      "Sparky: ADOT SDK → CloudWatch OTLP endpoints (AgentCore view)", 1070, 878, dash="5 4", width=2.4)

# ECR pulls
arrow([(1420, 782), (1420, 770), (1290, 770), (1290, 636), (1274, 636)], C["deploy"], "image pull", 1340, 764, dash="3 3", width=1.6)

# Legend
lx, ly = 30, 520
A(f'<rect x="{lx-8}" y="{ly-24}" width="150" height="190" rx="8" fill="#F8FAFC" stroke="#E2E8F0"/>')
A(f'<text x="{lx}" y="{ly-6}" font-size="12.5" font-weight="700" fill="{C["ink"]}">Legend</text>')
for i, (col, txt, dash) in enumerate([(C["req"], "user requests", None), (C["agent"], "agent tool calls", None),
                                       (C["ai"], "LLM calls", None), (C["db"], "data access", None),
                                       (C["tel"], "telemetry (OTel)", "5 4"), (C["deploy"], "deploy / build", "3 3")]):
    y = ly + 18 + i * 25
    da = f' stroke-dasharray="{dash}"' if dash else ""
    A(f'<path d="M {lx} {y} H {lx+34}" stroke="{col}" stroke-width="2.4"{da}/>')
    A(f'<text x="{lx+42}" y="{y+4}" font-size="11.5" fill="{C["ink"]}">{esc(txt)}</text>')

A('</svg>')
open(sys.argv[1], "w").write("\n".join(out))
print("wrote", sys.argv[1])
