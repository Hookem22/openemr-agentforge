"""One-off script generating presentation-ready PNG charts summarizing the Sonnet-vs-Haiku model
trade-off and cost-at-scale projections, for a business (non-technical) audience.

Source data: Gauntlet/Week 1/MODEL_TRADEOFF.md (measured Sonnet-vs-Haiku cost/latency/accuracy) and
Gauntlet/Week 1/COST_ANALYSIS.md (naive vs. architecture-adjusted cost projections at 100/1K/10K/100K users).
Not wired into any app code path -- run manually, ad hoc, whenever these source docs change and the
charts need refreshing. matplotlib is not added to requirements.txt since it's a one-off authoring tool,
not a runtime dependency of the agent service.

Usage: source ../venv/bin/activate && pip install matplotlib && python3 generate_charts.py
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
    return f"${x:.0f}"


# ---------------------------------------------------------------------------
# Chart 1: Sonnet vs. Haiku -- cost, latency, accuracy (measured, Gauntlet/Week 1/MODEL_TRADEOFF.md)
# ---------------------------------------------------------------------------
fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
fig.suptitle(
    "Clinical Co-Pilot: Sonnet vs. Haiku (measured)", fontsize=16, fontweight="bold", y=1.03
)

models = ["Sonnet\n(current)", "Haiku\n(candidate)"]
colors = [NAVY, TEAL]

# Cost per turn
ax = axes[0]
cost_vals = [0.0197, 0.00617]
bars = ax.bar(models, cost_vals, color=colors, width=0.55)
ax.set_title("Cost per Chat Turn", fontweight="bold")
ax.set_ylabel("USD")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.3f}"))
for b, v in zip(bars, cost_vals):
    ax.text(b.get_x() + b.get_width() / 2, v, f"${v:.4f}", ha="center", va="bottom", fontweight="bold")
ax.text(0.5, -0.32, "Haiku is ~69% cheaper per turn\n(0.31x Sonnet's cost)", transform=ax.transAxes,
        ha="center", fontsize=10, color=GRAY)
ax.spines[["top", "right"]].set_visible(False)

# Latency
ax = axes[1]
lat_vals = [11.50, 5.10]
bars = ax.bar(models, lat_vals, color=colors, width=0.55)
ax.set_title("Mean Response Latency", fontweight="bold")
ax.set_ylabel("Seconds")
for b, v in zip(bars, lat_vals):
    ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}s", ha="center", va="bottom", fontweight="bold")
ax.text(0.5, -0.32, "Haiku ~2.3x faster\n(caveat: samples not perfectly comparable)",
        transform=ax.transAxes, ha="center", fontsize=10, color=GRAY)
ax.spines[["top", "right"]].set_visible(False)

# Accuracy
ax = axes[2]
acc_vals = [100, 100]
bars = ax.bar(models, acc_vals, color=colors, width=0.55)
ax.set_title("Eval Suite Pass Rate", fontweight="bold")
ax.set_ylabel("% tests passed")
ax.set_ylim(0, 115)
for b, v in zip(bars, acc_vals):
    ax.text(b.get_x() + b.get_width() / 2, v + 2, "27/27", ha="center", va="bottom", fontweight="bold")
ax.text(0.5, -0.32, "No accuracy regression detected\n(incl. all safety-critical checks)",
        transform=ax.transAxes, ha="center", fontsize=10, color=GRAY)
ax.spines[["top", "right"]].set_visible(False)

fig.text(
    0.5, -0.06,
    "Source: Gauntlet/Week 1/MODEL_TRADEOFF.md -- real measured data (Langfuse traces + eval suite), not estimates.",
    ha="center", fontsize=9, color=GRAY, style="italic",
)
fig.tight_layout()
fig.savefig("model_tradeoff_sonnet_vs_haiku.png", dpi=170, bbox_inches="tight")
plt.close(fig)


# ---------------------------------------------------------------------------
# Chart 2 & 3 use small multiples (one linear-scale panel per tier) instead of a single log-scale
# chart. A shared log scale made the actual within-tier bar-height difference (the % change that
# matters to a business reader) look nearly invisible, since a 100,000x range across tiers dwarfs a
# 10-30% difference within one tier. Each panel below is scaled to its own tier's data, so bar
# heights are honest and proportional to the real percentage difference at that tier.
# ---------------------------------------------------------------------------
tiers = ["100 users", "1,000 users", "10,000 users", "100,000 users"]
width = 0.55


def small_multiples_cost_chart(values_a, values_b, label_a, label_b, color_a, color_b, title, caption, filename):
    fig, axes = plt.subplots(1, 4, figsize=(14, 5))
    fig.suptitle(title, fontsize=16, fontweight="bold", y=1.05)
    for i, (ax, tier, a, b) in enumerate(zip(axes, tiers, values_a, values_b)):
        bars = ax.bar([0, 1], [a, b], color=[color_a, color_b], width=width)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["Current", "New"], fontsize=10)
        ax.set_title(tier, fontweight="bold", fontsize=13)
        ax.set_ylim(0, max(a, b) * 1.35)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(money))
        for b_, v in zip(bars, [a, b]):
            ax.text(b_.get_x() + b_.get_width() / 2, v, money(v), ha="center", va="bottom", fontweight="bold", fontsize=10)
        pct = (b / a - 1) * 100
        chg = f"{pct:+.0f}%"
        ax.text(0.5, 1.22, chg, transform=ax.transAxes, ha="center", fontsize=13, fontweight="bold",
                color=(ORANGE if pct > 0 else NAVY))
        ax.spines[["top", "right"]].set_visible(False)
        if i > 0:
            ax.spines["left"].set_visible(False)
    # Shared legend
    handles = [plt.Rectangle((0, 0), 1, 1, color=color_a), plt.Rectangle((0, 0), 1, 1, color=color_b)]
    fig.legend(handles, [label_a, label_b], loc="upper center", bbox_to_anchor=(0.5, 0.98), ncol=2, frameon=False, fontsize=11)
    fig.text(0.5, -0.05, caption, ha="center", fontsize=9, color=GRAY, style="italic")
    fig.tight_layout(rect=[0, 0, 1, 0.88])
    fig.savefig(filename, dpi=170, bbox_inches="tight")
    plt.close(fig)


# Chart 2: Naive vs. architecture-adjusted monthly cost at scale (Gauntlet/Week 1/COST_ANALYSIS.md)
naive = [2900, 29000, 289000, 2890000]
adjusted = [2930, 26400, 250000, 1985000]
small_multiples_cost_chart(
    naive, adjusted,
    "Naive projection (linear $/turn x users)", "With planned architecture (caching, autoscaling, tiering)",
    GRAY, TEAL,
    "Projected Monthly Cost at Scale: Naive vs. Planned Architecture",
    "Source: Gauntlet/Week 1/COST_ANALYSIS.md. Each panel is its own scale -- compare the % change within a panel,\n"
    "not bar heights across panels (the dollar range across tiers spans 100 users to 100,000 users).",
    "cost_at_scale_naive_vs_adjusted.png",
)

# Chart 3: Cost impact of adopting Haiku, at scale (illustrative, derived from measured 0.31x ratio)
haiku_ratio = 0.31
sonnet_adjusted = adjusted
haiku_naive_equivalent = [n * haiku_ratio for n in naive]
small_multiples_cost_chart(
    sonnet_adjusted, haiku_naive_equivalent,
    "Sonnet, with planned architecture (current model, optimized)", "Haiku, no other changes (measured 0.31x cost ratio)",
    NAVY, ORANGE,
    "What If We Switched to Haiku? Monthly Cost by Scale",
    "Illustrative: applies the measured Sonnet-vs-Haiku cost ratio to each scale tier. Each panel is its own\n"
    "scale -- compare the % change within a panel, not bar heights across panels. Accuracy showed no regression\n"
    "on our eval suite, but a full production switch should follow further manual review (Gauntlet/Week 1/MODEL_TRADEOFF.md).",
    "haiku_switch_cost_impact.png",
)

# ---------------------------------------------------------------------------
# Chart 4: Load/stress test summary (Gauntlet/Week 1/LOADTEST.md) -- latency vs. concurrency, and
# reliability/resource headroom. Linear x-axis (concurrency only spans 1-200, no need for log
# here) so the real "flat then rising" latency shape reads honestly.
# ---------------------------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5), gridspec_kw={"width_ratios": [1.6, 1]})
fig.suptitle("Clinical Co-Pilot: Load Test Summary (deployed, 1-200 concurrent users)",
             fontsize=16, fontweight="bold", y=1.04)

users_all = [1, 5, 10, 20, 30, 40, 50, 60, 75, 100, 125, 150, 200]
p50_all = [25.65, 11.12, 12.41, 12.47, 9.41, 12.33, 13.68, 15.40, 16.90, 25.77, 29.74, 36.54, 43.73]
p95_all = [25.65, 35.28, 28.79, 27.12, 26.11, 25.81, 31.55, 31.19, 38.90, 48.77, 49.87, 42.83, 55.39]
p99_all = [25.65, 35.28, 28.79, 28.86, 29.18, 33.90, 34.66, 34.27, 43.07, 48.77, 55.16, 46.00, 57.62]

ax1.plot(users_all, p99_all, marker="o", color=ORANGE, linewidth=2, label="p99")
ax1.plot(users_all, p95_all, marker="o", color=TEAL, linewidth=2, label="p95")
ax1.plot(users_all, p50_all, marker="o", color=NAVY, linewidth=2.5, label="p50 (median)")
ax1.axvspan(50, 200, color=LIGHT_GRAY, alpha=0.5, zorder=0)
ax1.set_title("Response Latency vs. Concurrent Users", fontweight="bold")
ax1.set_xlabel("Concurrent users")
ax1.set_ylabel("Seconds")
ax1.legend(frameon=False, loc="upper left")
ax1.spines[["top", "right"]].set_visible(False)
ax1.text(0.98, 0.03, "Flat 8-35s through 50 users, then rises\nas requests queue behind a single worker",
          transform=ax1.transAxes, ha="right", fontsize=9, color=GRAY, style="italic")

categories = ["CPU\n(peak)", "Memory\n(peak)"]
util_vals = [16.7, 25.7]
bars = ax2.bar(categories, util_vals, color=[NAVY, TEAL], width=0.5)
ax2.set_ylim(0, 100)
ax2.set_ylabel("% of container limit")
ax2.set_title("Resource Headroom at 200 Users", fontweight="bold")
for b, v in zip(bars, util_vals):
    ax2.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.0f}%", ha="center", fontweight="bold")
ax2.axhline(100, color=GRAY, linewidth=1, linestyle="--")
ax2.spines[["top", "right"]].set_visible(False)
ax2.text(0.5, -0.24, "0% container-level errors, 1-200 users.\nNo CPU/memory bottleneck found.",
          transform=ax2.transAxes, ha="center", fontsize=9, color=GRAY, style="italic")

fig.text(
    0.5, -0.08,
    "Source: Gauntlet/Week 1/LOADTEST.md. Latency is dominated by the LLM/tool-call round trip, not our infrastructure --\n"
    "CPU and memory stay well under capacity even at 200 users. One earlier run saw a transient 22% edge-layer\n"
    "(502) error rate at 50 users, not reproduced in later runs -- see LOADTEST.md for the full reconciliation.",
    ha="center", fontsize=9, color=GRAY, style="italic",
)
fig.tight_layout()
fig.savefig("loadtest_summary.png", dpi=170, bbox_inches="tight")
plt.close(fig)

print("Wrote: model_tradeoff_sonnet_vs_haiku.png, cost_at_scale_naive_vs_adjusted.png, haiku_switch_cost_impact.png, loadtest_summary.png")
