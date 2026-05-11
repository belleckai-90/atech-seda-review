"""SEDA Audit Reviewer — Streamlit web app with auth, packages, and PDF download.

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.database import (
    init_db, get_config, set_config,
    get_user_by_id, refresh_user, update_user_credits, set_user_active, set_user_role,
    get_all_users, get_packages, upsert_package, is_staff_role,
    approve_user, disable_user, get_pending_users,
    create_transaction, get_all_transactions,
    create_review, update_review, get_user_reviews, get_review, get_all_reviews,
    create_company, get_all_companies, get_company, update_company,
    set_user_company, get_user_features,
)
from src.auth import login, register, request_password_reset, reset_password

import extra_streamlit_components as stx

def _cookie_manager():
    if "_cm" not in st.session_state:
        st.session_state["_cm"] = stx.CookieManager(key="atech_cm")
    return st.session_state["_cm"]

_HERE        = Path(__file__).parent
_PROMPTS_DIR = _HERE / "prompts"
_REFS_DIR    = _HERE / "references"
_FINDS_LIB   = _HERE / "findings_library" / "common_findings.md"
_OUTPUTS_DIR = _HERE / "outputs"
_LOGO_PATH   = _HERE / "assets" / "logo.png"

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Atech.AI | SEDA Audit Reviewer",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  [data-testid="stSidebar"] { background: #1A2E4A; }
  [data-testid="stSidebar"] * { color: #FFFFFF !important; }
  [data-testid="stSidebar"] .stSelectbox label,
  [data-testid="stSidebar"] .stTextInput label { color: #AAC8D8 !important; font-size:0.82rem; }
  [data-testid="stSidebar"] hr { border-color: #2E4A6A; }
  [data-testid="stSidebar"] .stButton > button { color: #FFFFFF !important; background-color: rgba(255,255,255,0.08) !important; border: 1px solid rgba(255,255,255,0.15) !important; }
  [data-testid="stSidebar"] .stButton > button:hover { background-color: rgba(255,255,255,0.18) !important; }
  [data-testid="stSidebar"] .stButton > button p { color: #FFFFFF !important; }
  .main-title  { font-size:1.9rem; font-weight:700; color:#1A2E4A; }
  .sub-title   { font-size:0.95rem; color:#5A6A7A; margin-bottom:1.2rem; }
  .card        { background:#fff; border:1px solid #DDE5EE; border-radius:8px;
                 padding:1rem 1.2rem; margin-bottom:0.5rem; }
  .card-crit   { border-left:4px solid #CC2A2A; background:#FFF0F0; padding:0.7rem 1rem; border-radius:4px; margin:0.3rem 0; }
  .card-major  { border-left:4px solid #E8631A; background:#FFF8F0; padding:0.7rem 1rem; border-radius:4px; margin:0.3rem 0; }
  .card-mod    { border-left:4px solid #007B8A; background:#EAF6F8; padding:0.7rem 1rem; border-radius:4px; margin:0.3rem 0; }
  .card-minor  { border-left:4px solid #9B9B9B; background:#F4F6F8; padding:0.7rem 1rem; border-radius:4px; margin:0.3rem 0; }
  .gr-pass     { border-left:4px solid #1E8A44; background:#EAF8EF; padding:0.7rem 1rem; border-radius:4px; margin:0.3rem 0; }
  .gr-fail     { border-left:4px solid #CC2A2A; background:#FFF0F0; padding:0.7rem 1rem; border-radius:4px; margin:0.3rem 0; }
  .gr-warn     { border-left:4px solid #F5A623; background:#FFF8E1; padding:0.7rem 1rem; border-radius:4px; margin:0.3rem 0; }
  .pkg-card    { border:2px solid #007B8A; border-radius:10px; padding:1.2rem;
                 text-align:center; background:#F0FAFC; }
  .pkg-price   { font-size:1.8rem; font-weight:700; color:#1A2E4A; }
  .badge-admin { background:#E8631A; color:white; padding:2px 8px; border-radius:10px; font-size:0.75rem; }
  .badge-user  { background:#007B8A; color:white; padding:2px 8px; border-radius:10px; font-size:0.75rem; }
  .mismatch    { background:#FFF3CD; border:1px solid #FFC107; border-radius:6px;
                 padding:0.8rem; margin:0.5rem 0; }
  .metric-box  { background:white; border:1px solid #DDE5EE; border-radius:8px;
                 padding:0.8rem; text-align:center; }
  .m-val       { font-size:1.5rem; font-weight:700; color:#1A2E4A; }
  .m-lbl       { font-size:0.78rem; color:#5A6A7A; }
</style>
""", unsafe_allow_html=True)


# ── Session state init ────────────────────────────────────────────────────────
def _ss(key, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _init_session():
    _ss("logged_in",        False)
    _ss("user_id",          None)
    _ss("role",             "user")
    _ss("name",             "")
    _ss("credits",          0)
    _ss("features",         [])
    _ss("page",             "login")
    _ss("review_id",        None)
    # BEI persistent state
    _ss("bei_energy_data",  None)
    _ss("bei_profile",      None)
    _ss("bei_output_mode",  "Report & Dashboard")
    _ss("bei_docx_bytes",   None)
    _ss("bei_docx_stem",    None)

    # Restore session from cookie if not already logged in
    if not st.session_state.get("logged_in"):
        try:
            from datetime import datetime, timezone, timedelta
            cm = _cookie_manager()
            uid_str         = cm.get("auth_uid")
            last_active_str = cm.get("last_active")
            if uid_str and last_active_str:
                last_active = datetime.fromisoformat(last_active_str)
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=timezone.utc)
                idle_hours = (datetime.now(timezone.utc) - last_active).total_seconds() / 3600
                if idle_hours <= 24:
                    user = get_user_by_id(int(uid_str))
                    if user and user.get("is_active") and user.get("status") == "active":
                        _load_user(user, set_cookie=False)
                        now = datetime.now(timezone.utc)
                        cm.set("last_active", now.isoformat(), expires_at=now + timedelta(days=30))
                else:
                    cm.delete("auth_uid")
                    cm.delete("last_active")
        except Exception:
            pass


def _load_user(user: dict, set_cookie: bool = True):
    st.session_state.logged_in = True
    st.session_state.user_id   = user["id"]
    st.session_state.role      = user["role"]
    st.session_state.name      = user["name"]
    st.session_state.credits   = user["credits"]
    st.session_state.features  = get_user_features(user["id"])
    st.session_state.page      = "dashboard"
    if set_cookie:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        cm  = _cookie_manager()
        cm.set("auth_uid",    str(user["id"]),    expires_at=now + timedelta(days=30))
        cm.set("last_active", now.isoformat(),    expires_at=now + timedelta(days=30))


def _logout():
    try:
        cm = _cookie_manager()
        cm.delete("auth_uid")
        cm.delete("last_active")
    except Exception:
        pass
    for k in ["logged_in","user_id","role","name","credits","page","review_id"]:
        st.session_state.pop(k, None)
    st.rerun()


def _sync_credits():
    if st.session_state.get("user_id"):
        u = refresh_user(st.session_state.user_id)
        if u:
            st.session_state.credits  = u["credits"]
            st.session_state.features = get_user_features(u["id"])


def _nav(page: str):
    st.session_state.page = page
    st.rerun()


# ── Sidebar ───────────────────────────────────────────────────────────────────
def _sidebar():
    with st.sidebar:
        # Logo
        if _LOGO_PATH.exists():
            st.image(str(_LOGO_PATH), width=160)
        else:
            st.markdown("### **Atech Energy**")
        st.markdown("#### Atech.AI")
        st.divider()

        if st.session_state.get("logged_in"):
            role  = st.session_state.role
            badge = f'<span class="badge-{"admin" if is_staff_role(role) else "user"}">{role.upper()}</span>'
            st.markdown(f"**{st.session_state.name}** {badge}", unsafe_allow_html=True)
            cred = st.session_state.credits
            if is_staff_role(role):
                st.markdown("Credits: **Unlimited** ∞")
            else:
                st.markdown(f"Credits: **{cred}** review{'s' if cred!=1 else ''} remaining")
            st.divider()

            # Nav
            feats = st.session_state.get("features", [])
            if st.button("🏠  Dashboard",       use_container_width=True): _nav("dashboard")
            if "seda" in feats:
                if st.button("📋  SEDA Audit Review", use_container_width=True, key="nav_seda"): _nav("new_review")
            if "bei" in feats:
                if st.button("📊  BEI Report",  use_container_width=True): _nav("bei_report")
            if st.button("📦  Packages",        use_container_width=True): _nav("packages")
            if is_staff_role(role):
                st.divider()
                if st.button("⚙️  Admin Panel", use_container_width=True): _nav("admin")
            st.divider()
            if st.button("🚪  Log Out",         use_container_width=True): _logout()
        else:
            st.caption("Sign in to access the reviewer.")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _finding_html(f: dict) -> str:
    sev  = f.get("severity","").lower()
    css  = {"critical":"card-crit","major":"card-major","moderate":"card-mod","minor":"card-minor"}.get(sev,"card-minor")
    sec  = f.get("section_no","")
    name = f.get("section_name","")
    page = f.get("page_ref","")
    rem  = f.get("remarks","")
    tags = ", ".join(f.get("ground_rule_tags",[]))
    tag_bit = f"<br><small><b>Ground rules:</b> {tags}</small>" if tags else ""
    return (f'<div class="{css}"><b>§{sec} {name}</b> '
            f'<small style="color:#888">{page}</small><br>{rem}{tag_bit}</div>')


def _gr_html(gr: str, entry: dict) -> str:
    v   = entry.get("verdict","n/a")
    j   = entry.get("justification","")
    css = "gr-pass" if v.startswith("✓") else ("gr-warn" if v.startswith("⚠") else "gr-fail")
    return f'<div class="{css}"><b>{gr}: {v}</b><br><small>{j}</small></div>'


def _metric(col, label, value, warn=False):
    col.markdown(
        f'<div class="metric-box">'
        f'<div class="m-val" style="color:{"#CC2A2A" if warn else "#1A2E4A"}">{value}</div>'
        f'<div class="m-lbl">{label}</div></div>',
        unsafe_allow_html=True,
    )


# ── Pages ─────────────────────────────────────────────────────────────────────

def page_login():
    logo_tag = ""  # text branding only, no image

    # ── Global page CSS ───────────────────────────────────────────────────────
    st.markdown("""
    <style>
    [data-testid="stSidebar"]  { display:none !important; }
    [data-testid="stHeader"]   { display:none !important; }
    [data-testid="stToolbar"]  { display:none !important; }
    .block-container { padding:4vh 1rem 1rem !important; max-width:960px !important; }
    .stApp { background:#0a0d1a !important; }

    /* Merge the two columns into one card */
    div[data-testid="stHorizontalBlock"] {
        gap:0 !important; align-items:stretch;
        border-radius:20px; overflow:hidden;
        box-shadow:0 30px 80px rgba(0,0,0,0.65);
    }

    /* Input fields */
    div[data-testid="stTextInput"] input {
        background:#13151f !important;
        border:1px solid #2a2d3a !important;
        color:#e5e7eb !important;
        border-radius:8px !important;
    }
    div[data-testid="stTextInput"] input:focus {
        border-color:#6366f1 !important;
        box-shadow:0 0 0 3px rgba(99,102,241,.2) !important;
    }
    div[data-testid="stTextInput"] label { color:#9ca3af !important; font-size:.84rem !important; }

    /* Sign In button */
    div[data-testid="stForm"] .stFormSubmitButton > button {
        background:linear-gradient(135deg,#4f46e5 0%,#6366f1 100%) !important;
        color:white !important; border:none !important;
        border-radius:8px !important; font-weight:600 !important;
        height:2.75rem !important; letter-spacing:.4px;
        transition:all .3s !important;
    }
    div[data-testid="stForm"] .stFormSubmitButton > button:hover {
        background:linear-gradient(135deg,#4338ca 0%,#4f46e5 100%) !important;
        box-shadow:0 6px 24px rgba(99,102,241,.4) !important;
        transform:translateY(-1px) !important;
    }

    /* Secondary action buttons */
    .lp-btns .stButton > button {
        background:transparent !important;
        border:1px solid #2a2d3a !important;
        color:#9ca3af !important;
        border-radius:8px !important;
        transition:all .25s !important;
        font-size:.85rem !important;
    }
    .lp-btns .stButton > button:hover {
        border-color:#6366f1 !important;
        color:#a5b4fc !important;
    }

    /* Right panel background via column target */
    div[data-testid="stHorizontalBlock"] > div:last-child {
        background:#090b13 !important;
        border-left:1px solid #1c2035;
        padding:2.5rem 2rem !important;
    }
    </style>
    """, unsafe_allow_html=True)

    left_col, right_col = st.columns([1, 1], gap="small")

    # ── LEFT: animated canvas panel (all in one markdown block) ──────────────
    with left_col:
        st.markdown(f"""
        <div id="lp-wrap" style="position:relative;min-height:580px;overflow:hidden;
             background:linear-gradient(145deg,#0e1225,#141828);
             display:flex;align-items:center;justify-content:center;padding:2rem;">
          <canvas id="lp-cv" style="position:absolute;inset:0;width:100%;height:100%;"></canvas>
          <div style="position:relative;z-index:10;text-align:center;">
            {logo_tag}
            <div style="color:rgba(255,255,255,0.9);font-size:1.4rem;font-weight:700;
                        letter-spacing:1px;margin-bottom:.5rem;">Atech<span style="color:#818cf8;">.AI</span></div>
            <div style="color:rgba(255,255,255,0.35);font-size:.8rem;line-height:1.7;max-width:190px;margin:0 auto;">
              Intelligent tools for<br/>energy professionals
            </div>
          </div>
        </div>
        <script>
        (function(){{
          const cv = document.getElementById('lp-cv');
          if(!cv) return;
          const ctx = cv.getContext('2d');
          let w=0, h=0, dots=[], routes=[];

          function resize(){{
            const p = cv.parentElement;
            w = cv.width  = p.offsetWidth  || 440;
            h = cv.height = p.offsetHeight || 580;
            buildScene();
          }}

          function buildScene(){{
            dots = [];
            for(let x=8;x<w;x+=16) for(let y=8;y<h;y+=16)
              if(Math.random()>.3) dots.push({{x,y,o:Math.random()*.3+.05}});

            const cx=w/2, cy=h/2;
            routes = [
              {{pts:[{{x:cx*.35,y:cy*.45}},{{x:cx*.85,y:cy*.28}},{{x:cx*1.4,y:cy*.52}}],p:0,s:.004}},
              {{pts:[{{x:cx*.45,y:cy*1.45}},{{x:cx*1.0,y:cy*1.18}},{{x:cx*1.55,y:cy*1.38}}],p:.5,s:.005}},
              {{pts:[{{x:cx*.25,y:cy*.85}},{{x:cx*.75,y:cy*1.12}},{{x:cx*1.2,y:cy*.78}}],p:.2,s:.006}},
            ];
          }}

          function lerp(a,b,t){{return a+(b-a)*t;}}

          function draw(){{
            ctx.clearRect(0,0,w,h);
            dots.forEach(d=>{{
              ctx.beginPath();ctx.arc(d.x,d.y,1,0,Math.PI*2);
              ctx.fillStyle=`rgba(255,255,255,${{d.o}})`;ctx.fill();
            }});
            routes.forEach(r=>{{
              r.p=(r.p+r.s)%1;
              const segs=r.pts.length-1;
              const gp=r.p*segs;
              const si=Math.min(Math.floor(gp),segs-1);
              const sp=gp-si;
              ctx.beginPath();ctx.moveTo(r.pts[0].x,r.pts[0].y);
              r.pts.slice(1).forEach(p=>ctx.lineTo(p.x,p.y));
              ctx.strokeStyle='rgba(99,102,241,.3)';ctx.lineWidth=1.5;ctx.stroke();
              r.pts.forEach(p=>{{
                ctx.beginPath();ctx.arc(p.x,p.y,3.5,0,Math.PI*2);
                ctx.fillStyle='#6366f1';ctx.fill();
              }});
              const mx=lerp(r.pts[si].x,r.pts[si+1].x,sp);
              const my=lerp(r.pts[si].y,r.pts[si+1].y,sp);
              ctx.beginPath();ctx.arc(mx,my,7,0,Math.PI*2);
              ctx.fillStyle='rgba(129,140,248,.25)';ctx.fill();
              ctx.beginPath();ctx.arc(mx,my,3.5,0,Math.PI*2);
              ctx.fillStyle='#818cf8';ctx.fill();
            }});
            requestAnimationFrame(draw);
          }}

          window.addEventListener('resize',resize);
          setTimeout(resize,80);
          draw();
        }})();
        </script>
        """, unsafe_allow_html=True)

    # ── RIGHT: sign-in form ───────────────────────────────────────────────────
    with right_col:
        st.markdown("""
        <p style="color:#f3f4f6;font-size:1.75rem;font-weight:700;margin:0 0 .2rem 0;">Welcome back</p>
        <p style="color:#6b7280;font-size:.88rem;margin:0 0 1.6rem 0;">Sign in to your account</p>
        """, unsafe_allow_html=True)

        with st.form("login_form"):
            email    = st.text_input("Email address")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In  →", use_container_width=True, type="primary")

        st.markdown("<hr style='border:none;border-top:1px solid #1f2130;margin:1.2rem 0'/>",
                    unsafe_allow_html=True)

        st.markdown('<div class="lp-btns">', unsafe_allow_html=True)
        col_l, col_r = st.columns(2)
        with col_l:
            st.caption("No account yet?")
            if st.button("Create Account", use_container_width=True):
                _nav("register")
        with col_r:
            st.caption("Forgot password?")
            if st.button("Reset Password", use_container_width=True):
                _nav("forgot")
        st.markdown('</div>', unsafe_allow_html=True)

    if submitted:
        user, reason = login(email, password)
        if user:
            _load_user(user)
            st.rerun()
        elif reason == "pending":
            st.warning("Your account is pending approval. A staff member will activate it shortly.")
        elif reason == "disabled":
            st.error("Your account has been disabled. Please contact support.")
        else:
            st.error("Invalid email or password.")



def page_register():
    st.markdown('<p class="main-title">Create Account</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-title">Register to access Atech.AI</p>', unsafe_allow_html=True)

    with st.form("reg_form"):
        name     = st.text_input("Full name")
        email    = st.text_input("Email address")
        company  = st.text_input("Company / Organisation (optional)")
        password = st.text_input("Password (min 8 characters)", type="password")
        confirm  = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Register", use_container_width=True, type="primary")

    if submitted:
        if password != confirm:
            st.error("Passwords do not match.")
        else:
            ok, result = register(name, email, password, company)
            if ok:
                st.success("Account created! Your request is pending approval by our team. We'll notify you once your account is activated.")
                st.info("Please check back or contact kentphang@atechnologies.com.my if you need urgent access.")
            else:
                st.error(result)

    st.markdown("---")
    if st.button("← Back to Sign In"):
        _nav("login")


def page_dashboard():
    _sync_credits()
    name = st.session_state.name
    role = st.session_state.role

    st.markdown(f'<p class="main-title">Welcome, {name}</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-title">Your review history</p>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    reviews = get_user_reviews(st.session_state.user_id) if not is_staff_role(role) else get_all_reviews()
    done    = [r for r in reviews if r["status"] == "complete"]
    _metric(c1, "Total reviews", str(len(reviews)))
    _metric(c2, "Completed",     str(len(done)))
    if is_staff_role(role):
        _metric(c3, "Credits", "∞ (Staff)")
    else:
        _metric(c3, "Credits remaining", str(st.session_state.credits),
                warn=st.session_state.credits == 0)

    st.markdown("")
    col_a, col_b = st.columns([1, 5])
    with col_a:
        if st.button("+ New Review", type="primary"):
            _nav("new_review")

    st.divider()
    if not reviews:
        st.info("No reviews yet. Click **New Review** to get started.", icon="📂")
        return

    for r in reviews:
        with st.container():
            c1, c2, c3, c4 = st.columns([3, 2, 1.5, 1.5])
            c1.markdown(f"**{r['building_name'] or r['report_filename']}**")
            c2.caption(r["created_at"][:16] if r["created_at"] else "")
            status_color = {"complete":"🟢","running":"🟡","failed":"🔴","pending":"⚪"}.get(r["status"],"⚪")
            c3.markdown(f"{status_color} {r['status'].title()}")
            if r["status"] == "complete":
                if c4.button("View", key=f"view_{r['id']}"):
                    st.session_state.review_id = r["id"]
                    _nav("review_detail")
            st.divider()


def page_new_review():
    _sync_credits()
    role    = st.session_state.role
    credits = st.session_state.credits

    st.markdown('<p class="main-title">SEDA Audit Review</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-title">Upload your report and workbook to begin</p>', unsafe_allow_html=True)

    if not is_staff_role(role) and credits == 0:
        st.warning("You have no credits remaining. Please purchase a package to continue.", icon="💳")
        if st.button("View Packages"):
            _nav("packages")
        return

    col1, col2 = st.columns(2)
    with col1:
        docx_file = st.file_uploader("1. Energy Audit Report (.docx)", type=["docx"])
    with col2:
        xlsx_file = st.file_uploader("2. Audit Workbook (.xlsx)",        type=["xlsx"])

    report_type_choice = st.selectbox("Report type", ["Auto-detect","Commercial","Industrial"])
    dry_run = st.toggle("Dry run — Pass 1 only (no credit used, no API cost)", value=False)

    # Check API key
    api_key = get_config("ANTHROPIC_API_KEY")
    if not api_key and not dry_run:
        st.warning("The API key has not been configured by admin yet. "
                   "Dry run mode is available in the meantime.", icon="⚙️")

    run_ready = docx_file and xlsx_file and (dry_run or bool(api_key))
    if st.button("▶  Run Review", type="primary", disabled=not run_ready):
        _run_review(docx_file, xlsx_file, report_type_choice, dry_run, api_key)


def _run_review(docx_file, xlsx_file, report_type_choice, dry_run, api_key):
    from src.extract import extract_docx, extract_xlsx
    from src.verify  import build_verified_numbers

    user_id = st.session_state.user_id
    role    = st.session_state.role

    with tempfile.TemporaryDirectory() as tmp:
        docx_path = Path(tmp) / docx_file.name
        xlsx_path = Path(tmp) / xlsx_file.name
        docx_path.write_bytes(docx_file.read())
        xlsx_path.write_bytes(xlsx_file.read())

        review_id = create_review(user_id, docx_file.name, xlsx_file.name)

        with st.spinner("Pass 1 — extracting report…"):
            docx_data = extract_docx(docx_path)
        with st.spinner("Pass 1 — extracting workbook…"):
            xlsx_data = extract_xlsx(xlsx_path)
        with st.spinner("Pass 1 — verifying numbers…"):
            verified  = build_verified_numbers(xlsx_data)

        # Auto detect type
        if report_type_choice == "Auto-detect":
            text  = docx_data.get("text","").lower()
            rtype = "commercial" if text.count("commercial") > text.count("industrial") else "industrial"
        else:
            rtype = report_type_choice.lower()

        st.success(
            f"Pass 1 complete — {len(docx_data['text']):,} chars · "
            f"{len(docx_data['toc'])} TOC entries · type: {rtype}", icon="✅"
        )

        if dry_run:
            update_review(review_id, status="complete",
                          findings_json=json.dumps({"_dry_run": True, "verified": verified}, default=str))
            st.info("Dry run complete. No credit used.", icon="ℹ️")
            _show_verified_numbers(verified)
            return

        # Pass 2
        from src.review_pass import run_review_pass
        os.environ["ANTHROPIC_API_KEY"] = api_key

        with st.spinner("Pass 2 — LLM section review (≈60-90 s)…"):
            try:
                findings, usage = run_review_pass(
                    docx_data=docx_data, xlsx_data=xlsx_data,
                    verified=verified, report_type=rtype,
                    prompts_dir=_PROMPTS_DIR, references_dir=_REFS_DIR,
                    findings_library_path=_FINDS_LIB if _FINDS_LIB.exists() else None,
                )
            except Exception as exc:
                update_review(review_id, status="failed")
                st.error(f"Pass 2 failed: {exc}", icon="❌")
                return

        # Generate PDF
        from src.pdf_render import render_pdf
        with st.spinner("Generating PDF…"):
            pdf_bytes = render_pdf(findings, verified, docx_file.name)
            _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            stem     = Path(docx_file.name).stem
            pdf_path = _OUTPUTS_DIR / f"{stem}_{date.today().isoformat()}_{review_id}.pdf"
            pdf_path.write_bytes(pdf_bytes)

        building = findings.get("metadata", {}).get("building_name", "")
        update_review(
            review_id,
            status="complete",
            building_name=building,
            findings_json=json.dumps(findings, ensure_ascii=False),
            pdf_path=str(pdf_path),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cost_usd=usage.get("estimated_cost_usd", 0),
        )

        # Deduct credit (not for staff/superadmin)
        if not is_staff_role(role):
            update_user_credits(user_id, -1)
            _sync_credits()

        st.success("Review complete!", icon="✅")
        _show_verified_numbers(verified)
        _show_findings(findings, pdf_bytes, stem)


def _show_verified_numbers(verified: dict):
    st.subheader("Verified Numbers")
    b   = verified.get("baseline", {})
    reg = verified.get("regression", {})
    mm  = verified.get("cost_year_mismatch")
    r2  = reg.get("r_square")

    c1,c2,c3,c4,c5 = st.columns(5)
    _metric(c1, "Annual kWh",     f"{b.get('total_kwh',0):,.0f}" if b.get("total_kwh") else "—")
    _metric(c2, "Total Cost (RM)",f"{b.get('total_cost_rm',0):,.2f}" if b.get("total_cost_rm") else "—", warn=bool(mm))
    _metric(c3, "BEI kWh/m²/yr", str(b.get("bei_kwh_m2_from_sheet","—")))
    _metric(c4, "tCO2e",          f"{b.get('total_co2e_tonne',0):,.1f}" if b.get("total_co2e_tonne") else "—")
    _metric(c5, "R² (actual)",    f"{r2:.4f}" if r2 is not None else "—", warn=(r2 is not None and r2 < 0.75))

    if mm:
        st.markdown(
            f'<div class="mismatch">⚠️ <b>Cost-year mismatch:</b> '
            f'ESM year {mm["esm_baseline_year"]} (RM {mm.get("correct_cost_for_esm_year_rm",0):,.2f}) '
            f'vs kWh.Overall year {mm["kwh_overall_baseline_year"]} '
            f'(RM {mm.get("cost_for_kwh_overall_baseline_year_rm",0):,.2f}). '
            f'Overstatement: RM {abs(mm.get("cost_for_kwh_overall_baseline_year_rm",0)-mm.get("correct_cost_for_esm_year_rm",0)):,.2f}</div>',
            unsafe_allow_html=True,
        )
    if r2 is not None and r2 < 0.75:
        mr = reg.get("multiple_r", 0)
        st.markdown(
            f'<div class="mismatch">⚠️ <b>R² confusion risk:</b> '
            f'Multiple R = {mr:.4f} · R² = {r2:.4f} (below SEDA threshold 0.75)</div>',
            unsafe_allow_html=True,
        )


def _show_findings(findings: dict, pdf_bytes: bytes, stem: str):
    st.divider()
    st.subheader("Review Findings")

    sf       = findings.get("section_findings", [])
    critical = [f for f in sf if f.get("severity")=="Critical"]
    major    = [f for f in sf if f.get("severity")=="Major"]
    moderate = [f for f in sf if f.get("severity")=="Moderate"]
    minor    = [f for f in sf if f.get("severity")=="Minor"]

    c1,c2,c3,c4,c5 = st.columns(5)
    _metric(c1, "Sections reviewed", str(len(sf)))
    _metric(c2, "Critical",          str(len(critical)), warn=len(critical)>0)
    _metric(c3, "Major",             str(len(major)))
    _metric(c4, "Moderate",          str(len(moderate)))
    _metric(c5, "Minor",             str(len(minor)))

    st.markdown("")
    st.download_button(
        "⬇  Download PDF Report",
        data=pdf_bytes,
        file_name=f"{stem}_SEDA_Review_{date.today().isoformat()}.pdf",
        mime="application/pdf",
        type="primary",
    )

    tab1,tab2,tab3,tab4 = st.tabs([
        f"🔴 Critical ({len(critical)})",
        f"🟠 Major ({len(major)})",
        "Ground Rules",
        f"All ({len(sf)})",
    ])
    with tab1:
        [st.markdown(_finding_html(f), unsafe_allow_html=True) for f in critical] or st.success("None.", icon="✅")
    with tab2:
        [st.markdown(_finding_html(f), unsafe_allow_html=True) for f in major] or st.success("None.", icon="✅")
    with tab3:
        gr_labels = {"GR1":"Clarity","GR2":"Justified savings","GR3":"R² threshold","GR4":"No RE in EACG"}
        for gr, lbl in gr_labels.items():
            entry = findings.get("ground_rule_scorecard",{}).get(gr,{})
            st.markdown(f"**{gr} — {lbl}**")
            st.markdown(_gr_html(gr, entry), unsafe_allow_html=True)
    with tab4:
        sev_f = st.multiselect("Filter", ["Critical","Major","Moderate","Minor"],
                               default=["Critical","Major","Moderate","Minor"])
        for f in sf:
            if f.get("severity","Minor") in sev_f:
                st.markdown(_finding_html(f), unsafe_allow_html=True)


def page_review_detail():
    rid = st.session_state.get("review_id")
    if not rid:
        _nav("dashboard"); return
    review = get_review(rid)
    if not review:
        st.error("Review not found."); return

    st.markdown(f'<p class="main-title">{review["building_name"] or review["report_filename"]}</p>',
                unsafe_allow_html=True)
    st.caption(f"Reviewed: {review['created_at'][:16]}  •  Status: {review['status']}")

    if review["status"] != "complete":
        st.warning("This review is not yet complete."); return

    findings_json = review.get("findings_json","")
    if not findings_json:
        st.warning("No findings data."); return

    findings = json.loads(findings_json)

    if findings.get("_dry_run"):
        verified = findings.get("verified", {})
        _show_verified_numbers(verified)
        return

    # Regenerate PDF on-the-fly if needed
    pdf_path = Path(review.get("pdf_path",""))
    if pdf_path.exists():
        pdf_bytes = pdf_path.read_bytes()
    else:
        from src.pdf_render import render_pdf
        pdf_bytes = render_pdf(findings, {}, review["report_filename"])

    _show_verified_numbers({})
    _show_findings(findings, pdf_bytes, Path(review["report_filename"]).stem)

    if st.button("← Back to Dashboard"):
        _nav("dashboard")


def page_packages():
    st.markdown('<p class="main-title">Packages</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-title">Purchase credits to run SEDA audit reviews</p>',
                unsafe_allow_html=True)

    pkgs = get_packages()
    if not pkgs:
        st.info("No packages available yet. Please contact admin."); return

    cols = st.columns(len(pkgs))
    for col, pkg in zip(cols, pkgs):
        with col:
            price_str = f"RM {pkg['price_rm']:,.0f}" if pkg["price_rm"] > 0 else "Contact Admin"
            st.markdown(
                f'<div class="pkg-card">'
                f'<h3>{pkg["name"]}</h3>'
                f'<div class="pkg-price">{price_str}</div>'
                f'<p>{pkg["description"]}</p>'
                f'<p><b>{pkg["credits"]}</b> review credit{"s" if pkg["credits"]!=1 else ""}</p>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.markdown("")
            if st.button(f"Request — {pkg['name']}", key=f"pkg_{pkg['id']}",
                         use_container_width=True):
                st.info(
                    f"Thank you for your interest in the **{pkg['name']}** package.\n\n"
                    "Please contact Atech Energy to complete your purchase. "
                    "Credits will be added to your account upon confirmation.",
                    icon="📧",
                )

    st.divider()
    st.caption(
        "💡 Online payment coming soon. "
        "Contact us at **annabelleyeap@gmail.com** to purchase credits."
    )


# ── Admin panel ───────────────────────────────────────────────────────────────

def page_admin():
    if not is_staff_role(st.session_state.role):
        st.error("Access denied."); return

    st.markdown('<p class="main-title">Admin Panel</p>', unsafe_allow_html=True)

    viewer_is_superadmin = st.session_state.get("role") == "superadmin"

    tab_labels = ["👥 Users", "🏢 Companies", "📦 Packages", "💳 Transactions", "📋 All Reviews"]
    if viewer_is_superadmin:
        tab_labels = ["🔑 API Key"] + tab_labels

    tabs = st.tabs(tab_labels)
    tab_offset = 0

    if viewer_is_superadmin:
        tab_key = tabs[0]
        tab_offset = 1
        # ── API Key tab ──────────────────────────────────────────────────────
        with tab_key:
            st.subheader("Anthropic API Key")
            st.caption("This key is used for all Pass 2 LLM reviews. It is never shown to users.")
            current = get_config("ANTHROPIC_API_KEY")
            masked  = ("sk-ant-..." + current[-6:]) if len(current) > 10 else ("Not set" if not current else "Set")
            st.info(f"Current key: `{masked}`", icon="🔑")
            with st.form("apikey_form"):
                new_key = st.text_input("New API key", type="password", placeholder="sk-ant-api03-...")
                if st.form_submit_button("Save API Key", type="primary"):
                    if new_key.strip():
                        set_config("ANTHROPIC_API_KEY", new_key.strip())
                        st.success("API key saved.", icon="✅")
                    else:
                        st.error("Key cannot be empty.")

    tab_users, tab_co, tab_pkgs, tab_txn, tab_reviews = tabs[tab_offset], tabs[tab_offset+1], tabs[tab_offset+2], tabs[tab_offset+3], tabs[tab_offset+4]

    # ── Users tab ────────────────────────────────────────────────────────────
    with tab_users:
        st.subheader("User Summary")
        users   = get_all_users()
        reviews = get_all_reviews()

        # Aggregate reviews per user
        review_counts: dict[int, int] = {}
        last_review_date: dict[int, str] = {}
        for r in reviews:
            uid = r["user_id"]
            review_counts[uid] = review_counts.get(uid, 0) + 1
            ts = r.get("created_at", "")
            if ts and ts > last_review_date.get(uid, ""):
                last_review_date[uid] = ts

        # Pending approvals banner
        pending = get_pending_users()
        if pending:
            st.warning(f"⏳ **{len(pending)} account(s) awaiting approval.** See the Pending Approvals tab below.")

        # Top-level metrics (staff don't see superadmin row)
        visible_users = users if viewer_is_superadmin else [u for u in users if u["role"] != "superadmin"]
        active_count = sum(1 for u in visible_users if u["is_active"])
        admin_count  = sum(1 for u in visible_users if is_staff_role(u["role"]))
        mc1, mc2, mc3, mc4 = st.columns(4)
        _metric(mc1, "Total Users",      str(len(visible_users)))
        _metric(mc2, "Active",           str(active_count))
        _metric(mc3, "Admins",           str(admin_count))
        _metric(mc4, "Total Reviews Run", str(len(reviews)))

        st.markdown("")

        # Summary table header
        h = st.columns([2.2, 2.5, 0.9, 0.9, 0.9, 1, 2])
        for col, lbl in zip(h, ["**Name**","**Email**","**Role**","**Status**","**Credits**","**Reviews**","**Last Review**"]):
            col.markdown(lbl)
        st.markdown('<hr style="margin:4px 0 6px 0;border-color:#DDE5EE">', unsafe_allow_html=True)

        for u in users:
            if not viewer_is_superadmin and u["role"] == "superadmin":
                continue
            uid  = u["id"]
            rc   = review_counts.get(uid, 0)
            lr   = last_review_date.get(uid, "")
            lr   = lr[:10] if lr else "—"
            cred = "∞" if is_staff_role(u["role"]) else str(u["credits"])
            role_map = {"superadmin": "🔴 Superadmin", "staff": "🟠 Staff", "admin": "🟠 Staff", "customer": "🔵 Customer", "user": "🔵 Customer"}
            role_lbl   = role_map.get(u["role"], u["role"])
            status_lbl = "✅ Active" if u["is_active"] else "🔴 Inactive"

            row = st.columns([2.2, 2.5, 0.9, 0.9, 0.9, 1, 2])
            row[0].write(u["name"])
            row[1].caption(u["email"])
            row[2].write(role_lbl)
            row[3].write(status_lbl)
            row[4].write(cred)
            row[5].write(str(rc))
            row[6].caption(lr)

        st.divider()
        # Pending approvals section
        if pending:
            st.subheader("⏳ Pending Approvals")
            for u in pending:
                with st.expander(f"{u['name']} — {u['email']} ({u.get('company','') or 'No company'})"):
                    st.write(f"Registered: {u['created_at'][:10]}")
                    pa1, pa2 = st.columns(2)
                    if pa1.button("✅ Approve", key=f"approve_{u['id']}", type="primary"):
                        approve_user(u["id"])
                        st.success(f"Approved {u['name']}.")
                        st.rerun()
                    if pa2.button("❌ Reject & Delete", key=f"reject_{u['id']}"):
                        disable_user(u["id"])
                        st.warning(f"Rejected {u['name']}.")
                        st.rerun()
            st.divider()

        st.subheader("Manage Users")
        for u in users:
            role_tag = f"[{u['role'].upper()}]" if is_staff_role(u["role"]) else ""
            with st.expander(f"{u['name']} — {u['email']} {role_tag}"):
                c1, c2, c3, c4 = st.columns([2,2,2,2])
                c1.write(f"Company: {u['company'] or '—'}")
                c2.write(f"Credits: {u['credits']}")
                c3.write(f"Active: {'Yes' if u['is_active'] else 'No'}")
                c4.write(f"Joined: {u['created_at'][:10] if u['created_at'] else '—'}")

                gc1, gc2, gc3 = st.columns(3)
                with gc1:
                    delta = st.number_input("Grant credits", min_value=1, max_value=100,
                                            value=1, key=f"cr_{u['id']}")
                    if st.button("Grant", key=f"grnt_{u['id']}"):
                        update_user_credits(u["id"], delta)
                        create_transaction(u["id"], delta, 0, note=f"Admin grant ({delta} credits)")
                        st.success(f"Granted {delta} credit(s).")
                        st.rerun()

                with gc2:
                    if u["role"] == "superadmin":
                        st.caption("Protected")
                    else:
                        if st.button("Deactivate" if u["is_active"] else "Activate",
                                     key=f"act_{u['id']}"):
                            set_user_active(u["id"], not u["is_active"])
                            st.rerun()

                with gc3:
                    if u["role"] == "superadmin":
                        st.caption("Superadmin — role locked")
                    else:
                        new_role = "customer" if is_staff_role(u["role"]) else "staff"
                    if u["role"] != "superadmin" and st.button(f"Make {new_role.title()}", key=f"role_{u['id']}"):
                        set_user_role(u["id"], new_role)
                        st.success(f"{u['name']} is now {new_role}.")
                        st.rerun()

                # Company assignment
                all_companies = get_all_companies()
                co_map        = {c["name"]: c["id"] for c in all_companies}
                cur_co_name   = next((c["name"] for c in all_companies
                                      if c["id"] == u.get("company_id")), "— None —")
                co_options    = ["— None —"] + list(co_map.keys())
                new_co = st.selectbox("Company", co_options,
                                      index=co_options.index(cur_co_name),
                                      key=f"co_{u['id']}")
                if st.button("Set Company", key=f"setco_{u['id']}"):
                    set_user_company(u["id"], co_map.get(new_co))
                    st.success("Company updated.")
                    st.rerun()

    # ── Companies tab ────────────────────────────────────────────────────────
    with tab_co:
        st.subheader("Company Management")
        st.caption("Each company controls which modules its users can access.")

        feat_opts   = {"SEDA Audit Review": "seda", "BEI Report": "bei"}
        companies   = get_all_companies()

        for co in companies:
            co_feats = [f.strip() for f in co["features"].split(",")]
            status   = "✅ Active" if co["is_active"] else "🔴 Inactive"
            with st.expander(f"{co['name']} — {status} — modules: {co['features']}"):
                with st.form(f"co_form_{co['id']}"):
                    co_name = st.text_input("Company name", value=co["name"])
                    co_sel  = st.multiselect(
                        "Modules enabled", list(feat_opts.keys()),
                        default=[k for k, v in feat_opts.items() if v in co_feats],
                    )
                    co_act = st.checkbox("Active", value=bool(co["is_active"]))
                    if st.form_submit_button("Save"):
                        feat_str = ",".join(feat_opts[k] for k in co_sel)
                        update_company(co["id"], co_name, feat_str, co_act)
                        st.success("Saved.")
                        st.rerun()

                # Show members + add unassigned users
                all_users  = get_all_users()
                members    = [u for u in all_users if u.get("company_id") == co["id"]]
                unassigned = [u for u in all_users if not u.get("company_id")]
                member_str = ", ".join(u["name"] for u in members) if members else "none"
                st.markdown(f"**Members ({len(members)}):** {member_str}")
                if unassigned:
                    add_u = st.selectbox(
                        "Add user to this company",
                        ["—"] + [f"{u['name']} ({u['email']})" for u in unassigned],
                        key=f"add_u_{co['id']}",
                    )
                    if st.button("Add", key=f"add_btn_{co['id']}") and add_u != "—":
                        labels = [f"{u['name']} ({u['email']})" for u in unassigned]
                        set_user_company(unassigned[labels.index(add_u)]["id"], co["id"])
                        st.rerun()

        st.markdown("---")
        st.markdown("**Add new company**")
        with st.form("new_co_form"):
            nc_name = st.text_input("Company name")
            nc_sel  = st.multiselect("Modules enabled", list(feat_opts.keys()),
                                     default=list(feat_opts.keys()))
            if st.form_submit_button("Add Company", type="primary"):
                if nc_name:
                    feat_str = ",".join(feat_opts[k] for k in nc_sel)
                    create_company(nc_name, feat_str)
                    st.success(f"Company '{nc_name}' created.")
                    st.rerun()

    # ── Packages tab ─────────────────────────────────────────────────────────
    with tab_pkgs:
        st.subheader("Package Management")
        st.caption("Define your pricing tiers here. Prices are in RM.")

        pkgs = get_packages(active_only=False)
        for pkg in pkgs:
            with st.expander(f"{pkg['name']} — RM {pkg['price_rm']:,.0f} / {pkg['credits']} credits"):
                with st.form(f"pkg_form_{pkg['id']}"):
                    p_name  = st.text_input("Name",        value=pkg["name"])
                    p_desc  = st.text_input("Description", value=pkg["description"])
                    p_cred  = st.number_input("Credits", min_value=1, value=pkg["credits"])
                    p_price = st.number_input("Price (RM)", min_value=0.0, value=float(pkg["price_rm"]), step=50.0)
                    p_act   = st.checkbox("Active", value=bool(pkg["is_active"]))
                    if st.form_submit_button("Save Package"):
                        upsert_package(pkg["id"], p_name, p_desc, p_cred, p_price, p_act)
                        st.success("Saved.")
                        st.rerun()

        st.markdown("---")
        st.markdown("**Add new package**")
        with st.form("new_pkg_form"):
            n_name  = st.text_input("Name")
            n_desc  = st.text_input("Description")
            n_cred  = st.number_input("Credits", min_value=1, value=1)
            n_price = st.number_input("Price (RM)", min_value=0.0, value=500.0, step=50.0)
            if st.form_submit_button("Add Package", type="primary"):
                if n_name:
                    upsert_package(None, n_name, n_desc, n_cred, n_price, True)
                    st.success("Package added.")
                    st.rerun()

    # ── Transactions tab ─────────────────────────────────────────────────────
    with tab_txn:
        st.subheader("Transaction History")
        txns = get_all_transactions()
        if not txns:
            st.info("No transactions yet.")
        else:
            for t in txns:
                c1,c2,c3,c4 = st.columns([3,2,2,2])
                c1.write(f"{t['user_name']} ({t['user_email']})")
                c2.write(f"RM {t['amount_rm']:,.2f}")
                c3.write(f"+{t['credits_granted']} credits")
                c4.caption(f"{t['created_at'][:16]} — {t['note'] or t['payment_status']}")
                st.divider()

    # ── Reviews tab ──────────────────────────────────────────────────────────
    with tab_reviews:
        st.subheader("All Reviews")
        reviews = get_all_reviews()
        if not reviews:
            st.info("No reviews yet.")
        else:
            for r in reviews:
                c1,c2,c3,c4 = st.columns([3,2,1.5,1.5])
                c1.write(f"**{r['building_name'] or r['report_filename']}** ({r['user_name']})")
                c2.caption(r["created_at"][:16] if r["created_at"] else "")
                icon = {"complete":"🟢","running":"🟡","failed":"🔴"}.get(r["status"],"⚪")
                c3.write(f"{icon} {r['status'].title()}")
                if r.get("cost_usd"):
                    c4.caption(f"USD {r['cost_usd']:.4f}")
                st.divider()


def page_bei_report():
    from src.database import list_building_names, get_building_by_name, save_building_profile

    feats = st.session_state.get("features", [])
    if "bei" not in feats:
        st.error("Your account does not have access to the BEI Report module.")
        return

    st.markdown('<p class="main-title">Monthly BEI Report Generator</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="sub-title">Upload LAMPIRAN Excel — AI writes the Word report (EECA 2024 compliant)</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p style="color:#CC2A2A;font-size:0.85rem;margin:0 0 0.5rem 0">'
        'Fields marked <b>*</b> are mandatory</p>',
        unsafe_allow_html=True,
    )

    api_key = get_config("ANTHROPIC_API_KEY")

    # ── Site Memory ───────────────────────────────────────────────────────────
    saved_names = list_building_names()
    pre: dict = {}
    if saved_names:
        picked = st.selectbox(
            "Load saved building site",
            ["— New site —"] + saved_names,
            help="Select a previously visited site to pre-fill the form.",
        )
        if picked != "— New site —":
            pre = get_building_by_name(picked) or {}

    # ── File Upload ───────────────────────────────────────────────────────────
    xlsx_file = st.file_uploader("LAMPIRAN Excel file (.xlsx)", type=["xlsx"])

    st.divider()
    st.subheader("Building Profile")

    def _req(label: str):
        st.markdown(
            f"<span style='font-size:0.88rem;font-weight:600'>{label} "
            "<span style='color:#CC2A2A'>*</span></span>",
            unsafe_allow_html=True,
        )

    c1, c2 = st.columns(2)
    with c1:
        _req("Building Name")
        building_name = st.text_input(
            "Building Name", label_visibility="collapsed",
            value=pre.get("building_name", ""),
            placeholder="e.g. Menara Prestige",
        )
        _req("Client / Building Owner")
        client_name = st.text_input(
            "Client", label_visibility="collapsed",
            value=pre.get("client_name", ""),
            placeholder="e.g. XYZ Properties Sdn Bhd",
        )
        address_ovr = st.text_input("Address override (optional)", value=pre.get("address", ""))
        bt_opts = ["Office", "Shopping Mall", "Hotel", "Hospital", "Industrial", "Mixed Use", "Other"]
        building_type = st.selectbox(
            "Type of Building", bt_opts,
            index=bt_opts.index(pre.get("building_type", "Office"))
                  if pre.get("building_type") in bt_opts else 0,
        )
        year_completed = st.number_input(
            "Year of Completion", min_value=1950, max_value=2030,
            value=int(pre.get("year_completed", 2010) or 2010),
        )
        certifications = st.text_input(
            "Certifications (e.g. LEED Gold, GreenRE)",
            value=pre.get("certifications", ""),
        )

    with c2:
        _req("Gross Floor Area / GFA (m²)")
        gfa = st.number_input(
            "GFA m²", label_visibility="collapsed",
            min_value=0.0, value=float(pre.get("gfa", 0) or 0),
            step=100.0, format="%.2f",
        )
        _req("% of GFA Air Conditioned")
        ac_pct = st.number_input(
            "% AC", label_visibility="collapsed",
            min_value=0.0, max_value=100.0,
            value=float(pre.get("ac_pct", 0) or 0), step=1.0,
        )
        _req("Server Area (%)")
        server_area_pct = st.number_input(
            "Server %", label_visibility="collapsed",
            min_value=0.0, max_value=100.0,
            value=float(pre.get("server_area_pct", 0) or 0), step=1.0,
        )
        _req("Parking Area Enclosed (%)")
        parking_area_pct = st.number_input(
            "Parking %", label_visibility="collapsed",
            min_value=0.0, max_value=100.0,
            value=float(pre.get("parking_area_pct", 0) or 0), step=1.0,
        )
        _req("Net Floor Area / NFA (m²)")
        nfa = st.number_input(
            "NFA m²", label_visibility="collapsed",
            min_value=0.0, value=float(pre.get("nfa", 0) or 0),
            step=100.0, format="%.2f",
        )

    c3, c4 = st.columns(2)
    with c3:
        _req("Design Occupant Load Unit")
        du_opts = ["pax", "person", "m²"]
        design_load_unit = st.selectbox(
            "Design Load Unit", du_opts, label_visibility="collapsed",
            index=du_opts.index(pre.get("design_load_unit", "pax"))
                  if pre.get("design_load_unit") in du_opts else 0,
        )
        _req("Design Occupant Load")
        design_load = st.number_input(
            "Design Load", label_visibility="collapsed",
            min_value=0.0, value=float(pre.get("design_load", 0) or 0), step=10.0,
        )
        _req("Actual Occupant Load (%)")
        actual_load_pct = st.number_input(
            "Actual Load %", label_visibility="collapsed",
            min_value=0.0, max_value=100.0,
            value=float(pre.get("actual_load_pct", 0) or 0), step=1.0,
        )
    with c4:
        tariff_rate = st.number_input(
            "TNB Tariff Rate (sen/kWh)", min_value=0.0,
            value=float(pre.get("tariff_rate_sen", 36.5) or 36.5), step=0.5,
        )
        preparer_name = st.text_input(
            "Prepared By", value=pre.get("preparer_name", "Atech Energy Sdn Bhd"),
        )
        preparer_pos = st.text_input(
            "Position / Title", value=pre.get("preparer_position", "Energy Auditor"),
        )
        sub_date = st.date_input("Date of Submission", value=date.today())
        op_hours = st.text_input(
            "Operating Hours", value=pre.get("operating_hours", "Mon-Fri 0800-1800"),
        )

    st.divider()
    output_mode = st.radio(
        "Output mode",
        ["Report & Dashboard", "Report only", "Dashboard only"],
        horizontal=True,
        help="Dashboard only: instant charts from Excel, no AI narrative or Word document generated.",
    )

    dry_run = st.toggle("Dry run — extract only, no API call", value=not bool(api_key))
    if not api_key and not dry_run:
        st.warning("API key not configured. Enable dry run or set key in Admin Panel.", icon="⚙️")

    mandatory_ok = bool(building_name and client_name and gfa > 0 and nfa > 0)
    if not mandatory_ok:
        st.caption("⚠ Fill all mandatory fields (*) before generating.")
    if not xlsx_file:
        st.caption("⚠ Upload the LAMPIRAN Excel file to continue.")

    if st.button("▶  Generate BEI Report", type="primary",
                 disabled=not (xlsx_file and mandatory_ok and (dry_run or bool(api_key)))):
        # Clear previous results
        st.session_state.bei_energy_data = None
        st.session_state.bei_profile     = None
        st.session_state.bei_docx_bytes  = None
        st.session_state.bei_docx_stem   = None
        st.session_state.bei_output_mode = output_mode

        profile = {
            "building_name":      building_name,
            "client_name":        client_name,
            "address_override":   address_ovr,
            "building_type":      building_type,
            "year_completed":     int(year_completed),
            "gfa":                gfa,
            "ac_pct":             ac_pct,
            "server_area_pct":    server_area_pct,
            "parking_area_pct":   parking_area_pct,
            "nfa":                nfa,
            "design_load_unit":   design_load_unit,
            "design_load":        design_load,
            "actual_load_pct":    actual_load_pct,
            "certifications":     certifications,
            "tariff_rate_sen":    tariff_rate,
            "preparer_name":      preparer_name,
            "preparer_position":  preparer_pos,
            "submission_date":    sub_date.strftime("%d %B %Y"),
            "operating_hours":    op_hours,
        }
        save_building_profile(profile)
        _run_bei_report(xlsx_file, profile, dry_run, api_key, output_mode)

    # ── Persistent results (survive slider / widget reruns) ───────────────────
    ed = st.session_state.get("bei_energy_data")
    pf = st.session_state.get("bei_profile")
    om = st.session_state.get("bei_output_mode", "Report & Dashboard")
    if ed and pf:
        st.divider()
        _show_bei_numbers(ed, pf)
        db = st.session_state.get("bei_docx_bytes")
        ds = st.session_state.get("bei_docx_stem", "BEI_Report")
        if db and om != "Dashboard only":
            st.download_button(
                "⬇  Download Word Report (.docx)",
                data=db,
                file_name=f"{ds}_BEI_Report_{date.today().isoformat()}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
            )
        if om in ("Report & Dashboard", "Dashboard only"):
            _show_bei_dashboard(ed, pf)


def _run_bei_report(xlsx_file, profile: dict, dry_run: bool, api_key: str, output_mode: str = "Report & Dashboard"):
    from src.bei_extract import extract_bei_excel

    with tempfile.TemporaryDirectory() as tmp:
        xlsx_path = Path(tmp) / xlsx_file.name
        xlsx_path.write_bytes(xlsx_file.read())
        with st.spinner("Extracting LAMPIRAN data…"):
            energy_data = extract_bei_excel(xlsx_path)

    if not profile.get("building_name"):
        profile["building_name"] = energy_data.get("building_name", "")
    profile["address"] = profile.get("address_override") or energy_data.get("address", "")

    # Persist extracted data so dashboard survives widget reruns
    st.session_state.bei_energy_data = energy_data
    st.session_state.bei_profile     = profile

    gfa     = float(profile.get("gfa") or 1)
    total_a = float(energy_data.get("total_a", 0))
    bei     = round(total_a / gfa, 2)
    st.success(
        f"Extracted: **{energy_data.get('building_name', '—')}** · "
        f"Period: {energy_data.get('period_label', '—')} · "
        f"Total supply: {total_a:,.0f} kWh · BEI: {bei} kWh/m²/yr",
        icon="✅",
    )

    if dry_run or output_mode == "Dashboard only":
        if dry_run:
            st.info("Dry run complete — no API call made.", icon="ℹ️")
        return

    from src.bei_narrative import generate_bei_narrative
    from src.bei_render    import render_bei_docx

    with st.spinner("AI writing report narratives (≈30–60 s)…"):
        try:
            narratives = generate_bei_narrative(profile, energy_data, api_key)
        except Exception as exc:
            st.error(f"Narrative generation failed: {exc}", icon="❌")
            return

    with st.spinner("Building Word document…"):
        try:
            docx_bytes = render_bei_docx(profile, energy_data, narratives)
        except Exception as exc:
            st.error(f"Word render failed: {exc}", icon="❌")
            return

    stem = (profile.get("building_name") or "BEI_Report").replace(" ", "_")
    st.session_state.bei_docx_bytes = docx_bytes
    st.session_state.bei_docx_stem  = stem
    st.success("BEI Report generated!", icon="✅")


def _show_bei_numbers(energy_data: dict, profile: dict):
    gfa     = float(profile.get("gfa") or 1)
    total_a = float(energy_data.get("total_a") or 0)
    total_b = float(energy_data.get("total_b") or 0)
    total_c = float(energy_data.get("total_c") or 0)
    bei     = round(total_a / gfa, 2) if gfa else 0
    c1, c2, c3, c4, c5 = st.columns(5)
    _metric(c1, "Total Supply kWh (A)",  f"{total_a:,.0f}")
    _metric(c2, "Tenant kWh (B)",        f"{total_b:,.0f}")
    _metric(c3, "Net Landlord kWh (C)",  f"{total_c:,.0f}")
    _metric(c4, "BEI = A÷GFA (kWh/m²/yr)", str(bei), warn=bei > 200)
    star = ("5★" if bei <= 100 else "4★" if bei <= 135 else "3★" if bei <= 175 else "2★" if bei <= 220 else "1★")
    _metric(c5, "ST Star Rating", star)


def _show_bei_dashboard(energy_data: dict, profile: dict):
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.info("Install plotly (`pip install plotly`) to view the interactive dashboard.", icon="📊")
        return

    months = energy_data.get("months", [])
    m_a    = energy_data.get("monthly_a", [])
    m_b    = energy_data.get("monthly_b", [])
    m_c    = energy_data.get("monthly_c", [])
    gfa    = float(profile.get("gfa") or 1)
    tariff = float(profile.get("tariff_rate_sen", 36.5) or 36.5) / 100

    if not months:
        return

    st.divider()
    st.subheader("📊 BEI Dashboard")

    n = len(months)
    start_i, end_i = st.select_slider(
        "Select month range",
        options=list(range(n)),
        value=(0, n - 1),
        format_func=lambda i: months[i],
    )
    ms    = months[start_i : end_i + 1]
    a_s   = m_a[start_i : end_i + 1]
    b_s   = m_b[start_i : end_i + 1]
    c_s   = m_c[start_i : end_i + 1]
    costs = [v * tariff for v in a_s]
    bei_m = [round(v / gfa, 2) for v in a_s]   # BEI = A ÷ GFA each month

    mc1, mc2, mc3, mc4 = st.columns(4)
    _metric(mc1, "Total Supply kWh (A)", f"{sum(a_s):,.0f}")
    _metric(mc2, "Net Landlord kWh (C)", f"{sum(c_s):,.0f}")
    ann_bei = round(sum(a_s) * 12 / max(len(a_s), 1) / gfa, 1) if a_s else 0
    _metric(mc3, "Annualised BEI (A÷GFA)", f"{ann_bei} kWh/m²/yr", warn=ann_bei > 200)
    _metric(mc4, "Est. Cost (RM)",         f"{sum(costs):,.0f}")

    st.markdown("")

    fig1 = go.Figure()
    fig1.add_bar(name="Total Supply (A)", x=ms, y=a_s, marker_color="#1A2E4A")
    fig1.add_bar(name="Landlord Own (C)", x=ms, y=c_s, marker_color="#007B8A")
    fig1.add_bar(name="Tenant (B)",       x=ms, y=b_s, marker_color="#E8631A")
    fig1.update_layout(
        title="Monthly Electricity Consumption (kWh)",
        barmode="group",
        yaxis_title="kWh",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=360,
    )
    st.plotly_chart(fig1, use_container_width=True)

    col_l, col_r = st.columns(2)
    with col_l:
        fig2 = go.Figure()
        fig2.add_scatter(x=ms, y=bei_m, mode="lines+markers",
                          line=dict(color="#007B8A", width=2))
        fig2.update_layout(title="Monthly BEI (kWh/m²)", yaxis_title="kWh/m²", height=300)
        st.plotly_chart(fig2, use_container_width=True)
    with col_r:
        fig3 = go.Figure()
        fig3.add_bar(x=ms, y=costs, marker_color="#007B8A")
        fig3.update_layout(title="Monthly Cost Estimate (RM)", yaxis_title="RM", height=300)
        st.plotly_chart(fig3, use_container_width=True)


def page_forgot_password():
    st.markdown('<p class="main-title">Reset Password</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-title">Enter your email and we\'ll send a reset link.</p>', unsafe_allow_html=True)

    with st.form("forgot_form"):
        email = st.text_input("Email address")
        submitted = st.form_submit_button("Send Reset Link", use_container_width=True, type="primary")

    if submitted:
        request_password_reset(email)
        st.success("If an account exists for that email, a reset link has been sent. Check your inbox (and spam folder).")

    st.markdown("---")
    if st.button("← Back to Sign In"):
        _nav("login")


def page_reset_password(token: str):
    st.markdown('<p class="main-title">Set New Password</p>', unsafe_allow_html=True)

    with st.form("reset_form"):
        new_pw  = st.text_input("New password (min 8 characters)", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update Password", use_container_width=True, type="primary")

    if submitted:
        if new_pw != confirm:
            st.error("Passwords do not match.")
        else:
            ok, msg = reset_password(token, new_pw)
            if ok:
                st.success(msg)
                st.info("You can now sign in with your new password.")
                st.query_params.clear()
                if st.button("Go to Sign In"):
                    _nav("login")
            else:
                st.error(msg)


# ── Router ────────────────────────────────────────────────────────────────────
def main():
    init_db()
    _init_session()

    # Handle password reset links before anything else
    qp = st.query_params
    qp_page = qp.get("page", "")
    if qp_page == "reset":
        token = qp.get("token", "")
        page_reset_password(token)
        return

    _sidebar()

    page = st.session_state.get("page", "login")

    if not st.session_state.get("logged_in"):
        if page == "register":
            page_register()
        elif page == "forgot":
            page_forgot_password()
        else:
            page_login()
        return

    routes = {
        "dashboard":    page_dashboard,
        "new_review":   page_new_review,
        "packages":     page_packages,
        "review_detail":page_review_detail,
        "admin":        page_admin,
        "bei_report":   page_bei_report,
    }
    routes.get(page, page_dashboard)()


if __name__ == "__main__":
    main()
