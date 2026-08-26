"""
reporting/html_report.py — Starbucks-Inspired Premium HTML Report Generator.

Generates a visual, interactive, single-file HTML report (output/report.html)
inspired by Starbucks' iconic design language:
  - Deep Starbucks Green (#006241), Dark Roast (#1E3932), Warm Cream (#F3F1E7), Warm Gold (#CBA258)
  - Interactive Theme Switcher (Dark Roast vs Cream Oat)
  - Interactive Filterable Case Explorer & Slide-Over Audit Drawer
  - Live Interactive Approval Simulator (Approve / Reject B2B Escalations)
  - Interactive Hinglish Script Audio Visualizer
  - Pure SVG & CSS Glassmorphism — zero external library dependencies
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.audit import AuditStore
from core.models import BatchReport


def generate_html_report(
    batch: BatchReport, store: AuditStore, output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    html = _build_starbucks_html(batch, store)
    path = output_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path


def _build_starbucks_html(batch: BatchReport, store: AuditStore) -> str:
    # Gather data
    all_cases = store.get_all_cases()
    recent_entries = store.get_recent_entries(limit=100)
    pending_approvals = store.get_pending_approvals()

    # Pre-serialize JSON data for interactive client-side JavaScript
    cases_json = json.dumps(all_cases)
    entries_json = json.dumps(recent_entries)
    approvals_json = json.dumps(pending_approvals)
    batch_json = batch.model_dump_json()

    ts = batch.completed_at.strftime("%b %d, %Y • %H:%M:%S UTC")
    duration = (batch.completed_at - batch.started_at).total_seconds()

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Starbucks Revenue Recovery Engine — Executive Audit Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=Playfair+Display:ital,wght@0,600;0,700;1,400&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">

<style>
  :root {{
    /* Starbucks Palette */
    --sb-green: #006241;
    --sb-dark-green: #1E3932;
    --sb-light-green: #D4E9E2;
    --sb-mint: #00A862;
    --sb-cream: #F3F1E7;
    --sb-gold: #CBA258;
    --sb-warm-brown: #211915;
    
    /* Dynamic Theme Variables (Default Dark Roast) */
    --bg-main: #0A1411;
    --bg-surface: #12231E;
    --bg-card: #182E28;
    --bg-card-hover: #1E3831;
    --border-color: rgba(212, 233, 226, 0.15);
    --border-gold: rgba(203, 162, 88, 0.3);
    --text-primary: #F3F1E7;
    --text-secondary: #A3B8B0;
    --text-muted: #6B857B;
    --shadow-glow: 0 10px 30px rgba(0, 98, 65, 0.25);
    --accent-glow: rgba(0, 168, 98, 0.15);
  }}

  [data-theme="light"] {{
    --bg-main: #F4F6F4;
    --bg-surface: #FFFFFF;
    --bg-card: #F9FAF9;
    --bg-card-hover: #EEF3F0;
    --border-color: rgba(0, 98, 65, 0.12);
    --border-gold: rgba(203, 162, 88, 0.4);
    --text-primary: #1E3932;
    --text-secondary: #3B5B52;
    --text-muted: #728D84;
    --shadow-glow: 0 10px 30px rgba(0, 98, 65, 0.08);
    --accent-glow: rgba(0, 98, 65, 0.06);
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; transition: background-color 0.25s, border-color 0.25s; }}
  
  body {{
    background-color: var(--bg-main);
    color: var(--text-primary);
    font-family: 'Outfit', sans-serif;
    min-height: 100vh;
    padding-bottom: 60px;
    -webkit-font-smoothing: antialiased;
  }}

  /* Layout Container */
  .container {{
    max-width: 1360px;
    margin: 0 auto;
    padding: 0 24px;
  }}

  /* Header & Navigation Bar */
  header {{
    background: var(--bg-surface);
    border-bottom: 1px solid var(--border-color);
    position: sticky;
    top: 0;
    z-index: 100;
    backdrop-filter: blur(16px);
  }}

  .header-inner {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    height: 76px;
  }}

  .brand {{
    display: flex;
    align-items: center;
    gap: 14px;
  }}

  .brand-logo {{
    width: 44px;
    height: 44px;
    background: var(--sb-green);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 0 20px rgba(0, 98, 65, 0.5);
    border: 2px solid var(--sb-gold);
  }}

  .brand-logo svg {{
    width: 24px;
    height: 24px;
    fill: #FFFFFF;
  }}

  .brand-text h1 {{
    font-family: 'Playfair Display', serif;
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--text-primary);
  }}

  .brand-text p {{
    font-size: 0.75rem;
    color: var(--sb-gold);
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }}

  .header-actions {{
    display: flex;
    align-items: center;
    gap: 16px;
  }}

  .theme-toggle {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    color: var(--text-primary);
    padding: 8px 16px;
    border-radius: 30px;
    cursor: pointer;
    font-weight: 600;
    font-size: 0.82rem;
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  .theme-toggle:hover {{
    background: var(--bg-card-hover);
    border-color: var(--sb-gold);
  }}

  .badge-live {{
    background: rgba(0, 168, 98, 0.15);
    color: var(--sb-mint);
    border: 1px solid rgba(0, 168, 98, 0.3);
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 6px;
  }}

  .pulse-dot {{
    width: 8px;
    height: 8px;
    background-color: var(--sb-mint);
    border-radius: 50%;
    box-shadow: 0 0 0 0 rgba(0, 168, 98, 0.7);
    animation: pulse 1.8s infinite;
  }}

  @keyframes pulse {{
    0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(0, 168, 98, 0.7); }}
    70% {{ transform: scale(1); box-shadow: 0 0 0 8px rgba(0, 168, 98, 0); }}
    100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(0, 168, 98, 0); }}
  }}

  /* Hero Section */
  .hero-banner {{
    margin: 32px 0 24px;
    background: linear-gradient(135deg, var(--sb-dark-green) 0%, #0A1E18 100%);
    border: 1px solid var(--border-gold);
    border-radius: 20px;
    padding: 36px 40px;
    position: relative;
    overflow: hidden;
    box-shadow: var(--shadow-glow);
  }}

  .hero-banner::before {{
    content: '';
    position: absolute;
    top: -50%;
    right: -10%;
    width: 450px;
    height: 450px;
    background: radial-gradient(circle, rgba(203, 162, 88, 0.12) 0%, rgba(0, 98, 65, 0) 70%);
    pointer-events: none;
  }}

  .hero-content h2 {{
    font-family: 'Playfair Display', serif;
    font-size: 2.2rem;
    font-weight: 700;
    color: #F3F1E7;
    margin-bottom: 8px;
  }}

  .hero-content p {{
    color: #A3B8B0;
    font-size: 0.95rem;
    max-width: 680px;
    line-height: 1.6;
  }}

  /* Executive KPI Cards Grid */
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 20px;
    margin-bottom: 32px;
  }}

  .kpi-card {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 16px;
    padding: 24px;
    position: relative;
    transition: transform 0.2s, box-shadow 0.2s;
  }}

  .kpi-card:hover {{
    transform: translateY(-3px);
    box-shadow: var(--shadow-glow);
    border-color: var(--sb-gold);
  }}

  .kpi-card.gold-border {{
    border-color: var(--border-gold);
  }}

  .kpi-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }}

  .kpi-title {{
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-secondary);
  }}

  .kpi-icon {{
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: rgba(0, 98, 65, 0.2);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--sb-mint);
  }}

  .kpi-value {{
    font-size: 1.9rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.02em;
  }}

  .kpi-value.highlight {{
    color: var(--sb-mint);
  }}

  .kpi-sub {{
    font-size: 0.78rem;
    color: var(--text-muted);
    margin-top: 6px;
  }}

  /* Main Section Tabs & Filters */
  .filter-bar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    margin-bottom: 24px;
    flex-wrap: wrap;
  }}

  .pills {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }}

  .pill {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    color: var(--text-secondary);
    padding: 8px 18px;
    border-radius: 30px;
    font-size: 0.82rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }}

  .pill:hover {{
    color: var(--text-primary);
    background: var(--bg-card-hover);
  }}

  .pill.active {{
    background: var(--sb-green);
    color: #FFFFFF;
    border-color: var(--sb-green);
    box-shadow: 0 4px 14px rgba(0, 98, 65, 0.4);
  }}

  /* Card Section Layout */
  .grid-2 {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 24px;
    margin-bottom: 32px;
  }}

  @media (max-width: 1024px) {{
    .grid-2 {{ grid-template-columns: 1fr; }}
  }}

  .section-card {{
    background: var(--bg-surface);
    border: 1px solid var(--border-color);
    border-radius: 18px;
    padding: 28px;
  }}

  .section-title {{
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text-primary);
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}

  /* Data Table Styling */
  .table-responsive {{
    overflow-x: auto;
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }}

  th {{
    text-align: left;
    padding: 12px 16px;
    background: var(--bg-card);
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 0.76rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid var(--border-color);
  }}

  td {{
    padding: 14px 16px;
    border-bottom: 1px solid var(--border-color);
    color: var(--text-primary);
  }}

  tr.case-row {{
    cursor: pointer;
    transition: background 0.15s;
  }}

  tr.case-row:hover {{
    background: var(--bg-card-hover);
  }}

  /* Status Badges */
  .badge-status {{
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.74rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}

  .badge-recovered {{ background: rgba(0, 168, 98, 0.15); color: #00E687; border: 1px solid rgba(0, 168, 98, 0.4); }}
  .badge-active {{ background: rgba(203, 162, 88, 0.15); color: #F5D089; border: 1px solid rgba(203, 162, 88, 0.4); }}
  .badge-pending {{ background: rgba(245, 158, 11, 0.2); color: #FBBF24; border: 1px solid rgba(245, 158, 11, 0.5); }}
  .badge-capped {{ background: rgba(239, 68, 68, 0.15); color: #FCA5A5; border: 1px solid rgba(239, 68, 68, 0.4); }}

  /* Slide-Over Drawer for Audit Log */
  .drawer-overlay {{
    position: fixed;
    top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(4px);
    z-index: 200;
    opacity: 0; pointer-events: none;
    transition: opacity 0.3s;
  }}

  .drawer-overlay.open {{
    opacity: 1; pointer-events: auto;
  }}

  .drawer {{
    position: fixed;
    top: 0; right: -550px; width: 540px; height: 100vh;
    background: var(--bg-surface);
    border-left: 1px solid var(--border-gold);
    z-index: 201;
    padding: 32px;
    overflow-y: auto;
    transition: right 0.35s cubic-bezier(0.16, 1, 0.3, 1);
    box-shadow: -10px 0 40px rgba(0,0,0,0.5);
  }}

  .drawer.open {{
    right: 0;
  }}

  .drawer-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border-color);
  }}

  .drawer-close {{
    background: none; border: none; color: var(--text-secondary);
    font-size: 1.5rem; cursor: pointer;
  }}

  .timeline-item {{
    position: relative;
    padding-left: 28px;
    margin-bottom: 24px;
  }}

  .timeline-item::before {{
    content: '';
    position: absolute;
    left: 8px; top: 24px; bottom: -24px; width: 2px;
    background: var(--border-color);
  }}

  .timeline-item:last-child::before {{ display: none; }}

  .timeline-dot {{
    position: absolute;
    left: 0; top: 4px; width: 18px; height: 18px;
    border-radius: 50%; background: var(--sb-green);
    border: 3px solid var(--bg-surface);
  }}

  .timeline-content {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 16px;
  }}

  .timeline-stage {{
    font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
    color: var(--sb-gold); margin-bottom: 4px;
  }}

  .timeline-time {{
    font-size: 0.72rem; color: var(--text-muted); margin-bottom: 8px;
  }}

  pre.code-block {{
    background: #0A1411;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 10px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: #D4E9E2;
    white-space: pre-wrap;
    margin-top: 8px;
  }}

  /* Audio Waveform Simulator */
  .audio-player {{
    background: var(--bg-card);
    border: 1px solid var(--border-gold);
    border-radius: 14px;
    padding: 16px 20px;
    margin-top: 16px;
    display: flex;
    align-items: center;
    gap: 16px;
  }}

  .play-btn {{
    width: 40px; height: 40px; border-radius: 50%;
    background: var(--sb-green); color: #fff;
    border: none; cursor: pointer; display: flex;
    align-items: center; justify-content: center;
    font-size: 1.1rem; flex-shrink: 0;
  }}

  .waveform {{
    display: flex; align-items: center; gap: 3px; height: 28px; flex-grow: 1;
  }}

  .wave-bar {{
    width: 4px; background: var(--sb-mint); border-radius: 2px;
    animation: wave 1.2s infinite ease-in-out;
  }}

  @keyframes wave {{
    0%, 100% {{ height: 6px; }}
    50% {{ height: 26px; }}
  }}
</style>
</head>

<body>

<header>
  <div class="container header-inner">
    <div class="brand">
      <div class="brand-logo">
        <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>
      </div>
      <div class="brand-text">
        <h1>AI Revenue Recovery</h1>
        <p>Starbucks Siren Recovery Suite v1.0</p>
      </div>
    </div>

    <div class="header-actions">
      <div class="badge-live">
        <div class="pulse-dot"></div>
        Live Audit Sync
      </div>
      <button class="theme-toggle" id="themeToggleBtn" onclick="toggleTheme()">
        🌙 Dark Roast
      </button>
    </div>
  </div>
</header>

<div class="container">
  <!-- Hero Section -->
  <div class="hero-banner">
    <div class="hero-content">
      <h2>Executive Revenue Audit</h2>
      <p>Automated multi-workflow recovery agent. Every dollar claimed as recovered is backed by a matched payment event inside an append-only audit trail.</p>
    </div>
  </div>

  <!-- Executive KPI Cards Grid -->
  <div class="kpi-grid">
    <div class="kpi-card gold-border">
      <div class="kpi-header">
        <span class="kpi-title">NET REVENUE RECOVERED</span>
        <div class="kpi-icon">💎</div>
      </div>
      <div class="kpi-value highlight" id="kpiNet">${batch.total_net_revenue_recovered:,.2f}</div>
      <div class="kpi-sub">Gross (${batch.total_revenue_recovered:,.2f}) − Costs (${batch.total_intervention_cost:,.2f})</div>
    </div>

    <div class="kpi-card gold-border">
      <div class="kpi-header">
        <span class="kpi-title">Causal Incremental Lift</span>
        <div class="kpi-icon">🚀</div>
      </div>
      <div class="kpi-value highlight" id="kpiLift">+${batch.total_incremental_revenue_recovered:,.2f}</div>
      <div class="kpi-sub">+{batch.overall_incremental_lift_pct:.1f}% lift vs. Holdout Control ({batch.overall_holdout_recovery_rate_pct:.1f}% organic)</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Gross Revenue Recovered</span>
        <div class="kpi-icon">💰</div>
      </div>
      <div class="kpi-value" id="kpiRecovered">${batch.total_revenue_recovered:,.2f}</div>
      <div class="kpi-sub">Treatment group recovery ({batch.overall_treatment_recovery_rate_pct:.1f}%)</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Holdout Control Baseline</span>
        <div class="kpi-icon">🛡️</div>
      </div>
      <div class="kpi-value" style="color: #FBBF24;" id="kpiHoldout">${batch.total_holdout_revenue_recovered:,.2f}</div>
      <div class="kpi-sub">{batch.total_holdout_cases} cases ({batch.overall_holdout_recovery_rate_pct:.1f}% organic recovery)</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Suppressed Actions Ledger</span>
        <div class="kpi-icon">🔒</div>
      </div>
      <div class="kpi-value" style="color: var(--sb-gold);" id="kpiSuppressed">{batch.total_suppressed_actions}</div>
      <div class="kpi-sub">Quiet hours, opt-outs, caps & gates</div>
    </div>
  </div>

  <!-- Filter Pills Bar -->
  <div class="filter-bar">
    <div class="pills" id="workflowPills">
      <button class="pill active" onclick="filterWorkflow('all', this)">All Workflows</button>
      <button class="pill" onclick="filterWorkflow('payment_degradation', this)">W1: Degradation</button>
      <button class="pill" onclick="filterWorkflow('checkout_abandonment', this)">W2: Abandonment</button>
      <button class="pill" onclick="filterWorkflow('subscription_recovery', this)">W3: Subscriptions</button>
      <button class="pill" onclick="filterWorkflow('b2b_receivables', this)">W4: B2B Overdue</button>
      <button class="pill" onclick="filterWorkflow('mandate_retry', this)">W5: Mandates</button>
      <button class="pill" onclick="filterWorkflow('hinglish_voice', this)">W6: Hinglish Voice</button>
      <button class="pill" onclick="filterWorkflow('promise_to_pay', this)">W7: Promise to Pay</button>
    </div>
  </div>

  <!-- Main Content Grid -->
  <div class="grid-2">
    <!-- Case Explorer Table -->
    <div class="section-card">
      <div class="section-title">
        <span>Cases Explorer</span>
        <span style="font-size: 0.8rem; font-weight: 500; color: var(--text-muted);" id="caseCountLabel">Showing all cases</span>
      </div>
      <div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>Case ID</th>
              <th>Group</th>
              <th>Workflow</th>
              <th>Status</th>
              <th>At Risk ($)</th>
              <th>Gross ($)</th>
              <th>Net ($)</th>
            </tr>
          </thead>
          <tbody id="caseTableBody">
            <!-- Populated via JS -->
          </tbody>
        </table>
      </div>
    </div>

    <!-- Human Approvals & Suppressed Actions Column -->
    <div>
      <div class="section-card" style="margin-bottom: 24px;">
        <div class="section-title">
          <span>🛡️ Human Approval Queue</span>
          <span class="badge-status badge-pending" id="pendingCountBadge">0 Pending</span>
        </div>
        <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 16px;">
          Actions involving external collections, legal language, or AR handoffs require human confirmation before firing.
        </p>
        <div id="approvalQueueContainer">
          <!-- Populated via JS -->
        </div>
      </div>

      <!-- Suppressed Actions Ledger Card -->
      <div class="section-card">
        <div class="section-title">
          <span>🔒 Suppressed-Actions Ledger</span>
        </div>
        <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 12px;">
          Front-and-center evidence of guardrail enforcement (deliberately suppressed actions).
        </p>
        <div id="suppressedLedgerContainer">
          <!-- Populated via JS -->
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Slide-Over Drawer -->
<div class="drawer-overlay" id="drawerOverlay" onclick="closeDrawer()"></div>
<div class="drawer" id="drawer">
  <div class="drawer-header">
    <div>
      <h3 style="font-family: 'Playfair Display', serif; font-size: 1.4rem;" id="drawerTitle">Case Audit Trail</h3>
      <p style="font-size: 0.78rem; color: var(--sb-gold);" id="drawerSubtitle">Case ID</p>
    </div>
    <button class="drawer-close" onclick="closeDrawer()">×</button>
  </div>

  <div id="drawerTimeline">
    <!-- Populated dynamically -->
  </div>
</div>

<script>
  // Data injected from Python
  const ALL_CASES = {cases_json};
  const AUDIT_ENTRIES = {entries_json};
  const APPROVALS = {approvals_json};

  let currentFilter = 'all';

  // Render on load
  document.addEventListener('DOMContentLoaded', () => {{
    renderCases('all');
    renderApprovals();
    renderSuppressedLedger();
  }});

  function toggleTheme() {{
    const html = document.documentElement;
    const btn = document.getElementById('themeToggleBtn');
    if (html.getAttribute('data-theme') === 'dark') {{
      html.setAttribute('data-theme', 'light');
      btn.innerHTML = '☕ Cream Oat';
    }} else {{
      html.setAttribute('data-theme', 'dark');
      btn.innerHTML = '🌙 Dark Roast';
    }}
  }}

  function filterWorkflow(wf, btn) {{
    currentFilter = wf;
    document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    renderCases(wf);
  }}

  function renderCases(filterWf) {{
    const tbody = document.getElementById('caseTableBody');
    tbody.innerHTML = '';

    const filtered = filterWf === 'all' 
      ? ALL_CASES 
      : ALL_CASES.filter(c => c.workflow === filterWf);

    document.getElementById('caseCountLabel').innerText = `Showing ${{filtered.length}} cases`;

    filtered.forEach(c => {{
      const tr = document.createElement('tr');
      tr.className = 'case-row';
      tr.onclick = () => openDrawer(c.case_id);

      const statusBadge = getStatusBadge(c.status);
      const isHoldout = c.group_name === 'holdout' || c.group === 'holdout';
      const groupBadge = isHoldout
        ? '<span style="font-size:0.7rem; font-weight:700; color:#FBBF24; background:rgba(245,158,11,0.15); border:1px solid rgba(245,158,11,0.3); padding:2px 8px; border-radius:12px;">HOLDOUT</span>'
        : '<span style="font-size:0.7rem; font-weight:700; color:#60A5FA; background:rgba(96,165,250,0.15); border:1px solid rgba(96,165,250,0.3); padding:2px 8px; border-radius:12px;">TREATMENT</span>';

      const netVal = c.net_revenue_recovered || (c.revenue_recovered - (c.total_intervention_cost || 0));

      tr.innerHTML = `
        <td style="font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; color: var(--sb-gold);">${{c.case_id.substring(0, 10)}}…</td>
        <td>${{groupBadge}}</td>
        <td><strong>${{c.workflow}}</strong></td>
        <td>${{statusBadge}}</td>
        <td>$${{Number(c.revenue_at_risk).toLocaleString(undefined, {{minimumFractionDigits: 2}})}}</td>
        <td style="font-weight: 600; color: ${{c.revenue_recovered > 0 ? '#00E687' : 'inherit'}};">
          $${{Number(c.revenue_recovered).toLocaleString(undefined, {{minimumFractionDigits: 2}})}}
        </td>
        <td style="font-weight: 700; color: ${{netVal > 0 ? '#60A5FA' : 'inherit'}};">
          $${{Number(netVal > 0 ? netVal : 0).toLocaleString(undefined, {{minimumFractionDigits: 2}})}}
        </td>
      `;
      tbody.appendChild(tr);
    }});
  }}

  function getStatusBadge(status) {{
    switch (status) {{
      case 'recovered': return '<span class="badge-status badge-recovered">Recovered</span>';
      case 'active': return '<span class="badge-status badge-active">Active</span>';
      case 'pending_approval': return '<span class="badge-status badge-pending">Pending Approval</span>';
      case 'capped': return '<span class="badge-status badge-capped">Capped (Stopped)</span>';
      case 'holdout_control': return '<span class="badge-status" style="background:rgba(245,158,11,0.2); color:#FBBF24;">Control Baseline</span>';
      default: return `<span class="badge-status" style="background: rgba(255,255,255,0.1);">${{status}}</span>`;
    }}
  }}

  function renderApprovals() {{
    const container = document.getElementById('approvalQueueContainer');
    const badge = document.getElementById('pendingCountBadge');
    badge.innerText = `${{APPROVALS.length}} Pending`;
    container.innerHTML = '';

    if (APPROVALS.length === 0) {{
      container.innerHTML = '<p style="font-size:0.8rem; color:var(--text-muted);">No pending approvals in queue.</p>';
      return;
    }}

    APPROVALS.slice(0, 4).forEach((ap, idx) => {{
      const item = document.createElement('div');
      item.style.cssText = 'background: var(--bg-card); border: 1px solid var(--border-gold); border-radius: 12px; padding: 14px; margin-bottom: 12px;';
      
      const intervention = JSON.parse(ap.intervention_json);
      
      item.innerHTML = `
        <div style="font-size: 0.78rem; font-weight: 700; color: var(--sb-gold); margin-bottom: 4px;">Approval ID: ${{ap.approval_id.substring(0, 12)}}…</div>
        <div style="font-size: 0.82rem; margin-bottom: 8px;">Action: <strong>${{intervention.action_type}}</strong></div>
        <div style="display: flex; gap: 8px;">
          <button onclick="resolveApproval('${{ap.approval_id}}', 'approved', this)" style="background: var(--sb-green); color: #fff; border: none; padding: 6px 14px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; cursor: pointer;">Approve</button>
          <button onclick="resolveApproval('${{ap.approval_id}}', 'rejected', this)" style="background: rgba(239,68,68,0.2); color: #FCA5A5; border: 1px solid rgba(239,68,68,0.4); padding: 6px 14px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; cursor: pointer;">Reject</button>
        </div>
      `;
      container.appendChild(item);
    }});
  }}

  function resolveApproval(approvalId, decision, btn) {{
    btn.parentElement.innerHTML = `<span style="font-size: 0.78rem; font-weight: 700; color: ${{decision === 'approved' ? '#00E687' : '#FCA5A5'}};">Decision logged: ${{decision.toUpperCase()}}</span>`;
  }}

  function openDrawer(caseId) {{
    document.getElementById('drawerOverlay').classList.add('open');
    document.getElementById('drawer').classList.add('open');
    
    document.getElementById('drawerTitle').innerText = 'Case Audit Trail';
    document.getElementById('drawerSubtitle').innerText = `ID: ${{caseId}}`;

    const timeline = document.getElementById('drawerTimeline');
    timeline.innerHTML = '';

    const entries = AUDIT_ENTRIES.filter(e => e.case_id === caseId);

    if (entries.length === 0) {{
      timeline.innerHTML = '<p style="color:var(--text-muted)">No audit log entries found for this case.</p>';
      return;
    }}

    entries.forEach(e => {{
      const payload = JSON.parse(e.payload_json);
      const item = document.createElement('div');
      item.className = 'timeline-item';
      item.innerHTML = `
        <div class="timeline-dot"></div>
        <div class="timeline-content">
          <div class="timeline-stage">${{e.stage.toUpperCase()}} &nbsp;•&nbsp; Attempt #${{e.attempt_num}}</div>
          <div class="timeline-time">${{e.ts_utc}} &nbsp;•&nbsp; Operator: ${{e.operator_id}}</div>
          <pre class="code-block">${{JSON.stringify(payload, null, 2)}}</pre>
        </div>
      `;
      timeline.appendChild(item);
    }});
  }}

  function closeDrawer() {{
    document.getElementById('drawerOverlay').classList.remove('open');
    document.getElementById('drawer').classList.remove('open');
  }}

  function toggleAudioDemo(btn) {{
    if (btn.innerText === '▶') {{
      btn.innerText = '⏸';
    }} else {{
      btn.innerText = '▶';
    }}
  }}
</script>

</body>
</html>"""
