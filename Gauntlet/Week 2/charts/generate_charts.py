"""One-off script generating presentation-ready PNG charts summarizing Week 2's cost analysis and
load test findings, for a business (non-technical) audience -- same style/palette as
`Gauntlet/Week 1/charts/generate_charts.py`.

Source data: Gauntlet/Week 2/COST_ANALYSIS.md (ingestion/retrieval cost+latency, full-turn
comparison, cost-at-scale extension) and Gauntlet/Week 2/LOADTEST.md (real load test against the
deployed instance). All figures are the real, measured numbers already in those two documents --
this script only visualizes them, it doesn't compute anything new.

Not wired into any app code path -- run manually, ad hoc, whenever the source docs change and the
charts need refreshing. matplotlib is not added to requirements.txt since it's a one-off authoring
tool, not a runtime dependency of the agent service.

Usage: source ../../../agent/venv/bin/activate && pip install matplotlib && python3 generate_charts.py
"""
from __future__ import annotations

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

matplotlib.use("Agg")

NAVY = "#1f3864"
TEAL = "#2e8b8b"
ORANGE = "#e07b39"
GRAY = "#6b7280"
LIGHT_GRAY = "#e5e7eb"

plt.rcParams.update(
    {
        "font.size": 12,
        "axes.edgecolor": GRAY,
        "axes.labelcolor": "#111827",
        "text.color": "#111827",
        "xtick.color": "#111827",
        "ytick.color": "#111827",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
    }
)


def money(x, _pos=None):
    if x >= 1_000_000:
        return f"${x / 1_000_000:.1f}M"
    if x >= 1_000:
        return f"${x / 1_000:.0f}K"
    if x >= 1:
        return f"${x:.2f}"
    return f"${x:.4f}"


# ---------------------------------------------------------------------------
# Chart 1: Document extraction vs. evidence retrieval -- cost and latency, per call (measured,
# Gauntlet/Week 2/COST_ANALYSIS.md). Cost panel uses a log scale deliberately: the ~500x real gap
# between the two IS the finding (retrieval is essentially free next to extraction), and a linear
# scale would render the Voyage bar invisible rather than honestly showing the size of the gap.
# ---------------------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(10, 5.3))
fig.suptitle("Week 2: Document Extraction vs. Evidence Retrieval (measured)", fontsize=16, fontweight="bold", y=1.03)

flows = ["Extraction\n(Claude vision)", "Retrieval\n(Voyage)"]
colors = [NAVY, TEAL]

ax = axes[0]
cost_vals = [0.0252, 0.000046]
bars = ax.bar(flows, cost_vals, color=colors, width=0.55)
ax.set_yscale("log")
ax.set_title("Cost per Call", fontweight="bold")
ax.set_ylabel("USD (log scale)")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(money))
for b, v in zip(bars, cost_vals):
    ax.text(b.get_x() + b.get_width() / 2, v * 1.3, money(v) if v >= 1 else f"${v:.4f}" if v >= 0.001 else f"${v:.6f}",
            ha="center", va="bottom", fontweight="bold")
ax.text(0.5, -0.38, "Retrieval is ~550x cheaper per call --\nnegligible next to a single Claude turn",
        transform=ax.transAxes, ha="center", fontsize=10, color=GRAY)
ax.spines[["top", "right"]].set_visible(False)

ax = axes[1]
lat_vals = [11.17, 0.35]
bars = ax.bar(flows, lat_vals, color=colors, width=0.55)
ax.set_title("Mean Latency per Call", fontweight="bold")
ax.set_ylabel("Seconds")
for b, v in zip(bars, lat_vals):
    ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.2f}s", ha="center", va="bottom", fontweight="bold")
ax.text(0.5, -0.32, "Extraction (page image -> Claude vision)\nis the slow step, not retrieval",
        transform=ax.transAxes, ha="center", fontsize=10, color=GRAY)
ax.spines[["top", "right"]].set_visible(False)

fig.text(
    0.5, -0.06,
    "Source: Gauntlet/Week 2/COST_ANALYSIS.md -- real Langfuse trace data (extraction: 24 real samples, "
    "test-stub telemetry filtered out) and\nreal Voyage usage x live-fetched published pricing (not estimated).",
    ha="center", fontsize=9, color=GRAY, style="italic",
)
fig.tight_layout()
fig.savefig("extraction_vs_retrieval.png", dpi=170, bbox_inches="tight")
plt.close(fig)


# ---------------------------------------------------------------------------
# Chart 2: Full-turn cost/latency comparison -- plain Week 1-style chat turn vs. an evidence-routed
# Week 2 turn (two concrete, real, named traces -- Gauntlet/Week 2/COST_ANALYSIS.md).
# ---------------------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(10, 4.8))
fig.suptitle("Week 2: Plain Chat Turn vs. Evidence-Routed Turn (two real traces)", fontsize=16, fontweight="bold", y=1.03)

turns = ["Plain chat turn\n(Week 1-style)", "Evidence-routed turn\n(Week 2 addition)"]
colors2 = [GRAY, ORANGE]

ax = axes[0]
cost_vals2 = [0.0066, 0.0148]
bars = ax.bar(turns, cost_vals2, color=colors2, width=0.55)
ax.set_title("Total Turn Cost", fontweight="bold")
ax.set_ylabel("USD")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.4f}"))
for b, v in zip(bars, cost_vals2):
    ax.text(b.get_x() + b.get_width() / 2, v, f"${v:.4f}", ha="center", va="bottom", fontweight="bold")
ax.text(0.5, -0.32, "+124% cost for a synthesized,\nguideline-grounded answer",
        transform=ax.transAxes, ha="center", fontsize=10, color=GRAY)
ax.spines[["top", "right"]].set_visible(False)

ax = axes[1]
lat_vals2 = [5.78, 7.30]
bars = ax.bar(turns, lat_vals2, color=colors2, width=0.55)
ax.set_title("Total Turn Latency", fontweight="bold")
ax.set_ylabel("Seconds")
for b, v in zip(bars, lat_vals2):
    ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.2f}s", ha="center", va="bottom", fontweight="bold")
ax.text(0.5, -0.32, "+26% latency -- the extra Claude\nreasoning, not the retrieval hop itself",
        transform=ax.transAxes, ha="center", fontsize=10, color=GRAY)
ax.spines[["top", "right"]].set_visible(False)

fig.text(
    0.5, -0.08,
    "Source: Gauntlet/Week 2/COST_ANALYSIS.md, trace IDs 9616ee33...edb4 (plain) and bebab347...750b (evidence-routed).\n"
    "Caveat: the two questions differ in scope (broad chart pull vs. a targeted question) -- not a perfectly isolated\n"
    "'cost of adding RAG' figure; the evidence_retriever hop itself only added 0.30s and ~$0.00005 to its trace.",
    ha="center", fontsize=9, color=GRAY, style="italic",
)
fig.tight_layout()
fig.savefig("full_turn_comparison.png", dpi=170, bbox_inches="tight")
plt.close(fig)


# ---------------------------------------------------------------------------
# Chart 3: Load test -- /ingest vs. RAG-triggering /chat latency under concurrency, plus resource
# headroom (Gauntlet/Week 2/LOADTEST.md). Two separate latency panels (not one shared axis): the
# x-ranges differ (ingest tested to 5 users, rag-chat to 10) and, more importantly, the *shapes*
# differ sharply -- ingest rises steeply, rag-chat stays flat -- which is the actual finding, and
# forcing them onto one axis would visually bury it.
# ---------------------------------------------------------------------------
fig = plt.figure(figsize=(14, 5))
gs = fig.add_gridspec(1, 3, width_ratios=[1.2, 1.2, 0.9])
ax1, ax2, ax3 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1]), fig.add_subplot(gs[2])
fig.suptitle("Week 2 Load Test: /ingest vs. RAG-Triggering /chat (deployed instance)", fontsize=16, fontweight="bold", y=1.05)

ingest_users = [1, 3, 5]
ingest_mean = [15.90, 29.86, 60.50]
ingest_p95 = [15.90, 44.22, 71.68]
ax1.plot(ingest_users, ingest_p95, marker="o", color=ORANGE, linewidth=2, label="p95/max")
ax1.plot(ingest_users, ingest_mean, marker="o", color=NAVY, linewidth=2.5, label="mean")
ax1.set_title("/ingest Latency", fontweight="bold")
ax1.set_xlabel("Concurrent users")
ax1.set_ylabel("Seconds")
ax1.set_xticks(ingest_users)
ax1.legend(frameon=False, loc="upper left", fontsize=9)
ax1.spines[["top", "right"]].set_visible(False)
ax1.text(0.98, 0.03, "Steep: ~4x mean latency\nfrom 1 to 5 concurrent users", transform=ax1.transAxes,
         ha="right", fontsize=9, color=GRAY, style="italic")

rag_users = [1, 5, 10]
rag_mean = [24.31, 17.43, 19.20]
rag_p95 = [24.31, 25.34, 25.56]
ax2.plot(rag_users, rag_p95, marker="o", color=ORANGE, linewidth=2, label="p95")
ax2.plot(rag_users, rag_mean, marker="o", color=TEAL, linewidth=2.5, label="mean")
ax2.set_title("RAG-Triggering /chat Latency", fontweight="bold")
ax2.set_xlabel("Concurrent users")
ax2.set_ylim(0, max(ingest_p95) * 1.05)  # same y-scale as ingest panel, for an honest visual contrast
ax2.set_xticks(rag_users)
ax2.legend(frameon=False, loc="upper left", fontsize=9)
ax2.spines[["top", "right"]].set_visible(False)
ax2.text(0.98, 0.03, "Flat: behaves like ordinary /chat,\nnot like /ingest", transform=ax2.transAxes,
         ha="right", fontsize=9, color=GRAY, style="italic")

categories = ["CPU\n(peak)", "Memory\n(peak)"]
util_vals = [1.4, 18.4]
bars = ax3.bar(categories, util_vals, color=[NAVY, TEAL], width=0.5)
ax3.set_ylim(0, 100)
ax3.set_ylabel("% of container limit")
ax3.set_title("Resource Headroom", fontweight="bold")
for b, v in zip(bars, util_vals):
    ax3.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.1f}%", ha="center", fontweight="bold")
ax3.axhline(100, color=GRAY, linewidth=1, linestyle="--")
ax3.spines[["top", "right"]].set_visible(False)
ax3.text(0.5, -0.34, "0% errors at every level tested.\nGrowing latency is not resource\nexhaustion.",
         transform=ax3.transAxes, ha="center", fontsize=9, color=GRAY, style="italic")

fig.text(
    0.5, -0.08,
    "Source: Gauntlet/Week 2/LOADTEST.md. Both latency panels share the same y-axis scale for an honest visual\n"
    "comparison. /ingest's steeper curve reflects the single-uvicorn-worker architecture Week 1 already flagged,\n"
    "amplified by extraction's heavier per-call cost -- more urgent to fix for /ingest than for /chat.",
    ha="center", fontsize=9, color=GRAY, style="italic",
)
fig.tight_layout()
fig.savefig("loadtest_week2_summary.png", dpi=170, bbox_inches="tight")
plt.close(fig)


# ---------------------------------------------------------------------------
# Chart 4: Cost-at-scale -- Week 1 chat-only baseline vs. Week 2 blended (ingestion + retrieval mix
# factored in), across the same 4 tiers as Week 1's projection (Gauntlet/Week 2/COST_ANALYSIS.md).
# Same small-multiples pattern as Week 1's chart 2/3: each tier gets its own linear-scale panel so
# the real within-tier percentage difference (the number that matters) stays visually honest across
# a 100-to-100,000-user range that would otherwise dwarf it.
# ---------------------------------------------------------------------------
tiers = ["100 users", "1,000 users", "10,000 users", "100,000 users"]
week1_naive = [2900, 29000, 289000, 2890000]
week2_blended = [3255, 32550, 325500, 3255000]

fig, axes = plt.subplots(1, 4, figsize=(14, 5))
fig.suptitle("Projected Monthly Cost: Week 1 Chat-Only vs. Week 2 Blended", fontsize=16, fontweight="bold", y=1.05)
for i, (ax, tier, a, b) in enumerate(zip(axes, tiers, week1_naive, week2_blended)):
    bars = ax.bar([0, 1], [a, b], color=[GRAY, ORANGE], width=0.55)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Week 1", "Week 2"], fontsize=10)
    ax.set_title(tier, fontweight="bold", fontsize=13)
    ax.set_ylim(0, max(a, b) * 1.35)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(money))
    for b_, v in zip(bars, [a, b]):
        ax.text(b_.get_x() + b_.get_width() / 2, v, money(v), ha="center", va="bottom", fontweight="bold", fontsize=10)
    pct = (b / a - 1) * 100
    ax.text(0.5, 1.22, f"{pct:+.0f}%", transform=ax.transAxes, ha="center", fontsize=13, fontweight="bold", color=ORANGE)
    ax.spines[["top", "right"]].set_visible(False)
    if i > 0:
        ax.spines["left"].set_visible(False)
handles = [plt.Rectangle((0, 0), 1, 1, color=GRAY), plt.Rectangle((0, 0), 1, 1, color=ORANGE)]
fig.legend(handles, ["Week 1 (chat-only, naive)", "Week 2 (blended: chat + ~7% extraction + ~13% retrieval mix)"],
           loc="upper center", bbox_to_anchor=(0.5, 0.98), ncol=2, frameon=False, fontsize=10)
fig.text(
    0.5, -0.05,
    "Source: Gauntlet/Week 2/COST_ANALYSIS.md. Each panel is its own scale -- compare the % change within a panel.\n"
    "Assumes 1-in-15 turns include a document upload and 1-in-8 trigger evidence retrieval (stated modeling\n"
    "assumption, not measured -- no real multi-tenant usage exists yet to measure the mix from).",
    ha="center", fontsize=9, color=GRAY, style="italic",
)
fig.tight_layout(rect=[0, 0, 1, 0.88])
fig.savefig("cost_at_scale_week1_vs_week2.png", dpi=170, bbox_inches="tight")
plt.close(fig)

print("Wrote: extraction_vs_retrieval.png, full_turn_comparison.png, loadtest_week2_summary.png, cost_at_scale_week1_vs_week2.png")
