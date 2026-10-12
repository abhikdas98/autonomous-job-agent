import os, requests, streamlit as st
st.set_page_config(page_title="Autonomous Job Agent",layout="wide")
api = os.getenv("AGENT_API_BASE","http://127.0.0.1:8000").rstrip("/")
st.title("Autonomous Job Agent")
st.caption("Job discovery, relevance ranking, approvals, and monitoring")

def get(path, params=None):
    try:
        r=requests.get(api+path,params=params,timeout=8); r.raise_for_status(); return r.json()
    except Exception as e:
        st.error(f"API unavailable: {e}"); return None

def post(path, params=None, body=None):
    try:
        r=requests.post(api+path,params=params,json=body,timeout=90); r.raise_for_status(); return r.json()
    except Exception as e:
        st.error(f"Request failed: {e}"); return None

with st.sidebar:
    st.subheader("Agent controls")
    source=st.selectbox("Source",["demo","adzuna"])
    query=st.text_input("Role query","agentic AI engineer OR generative AI engineer OR GenAI developer")
    location=st.text_input("Location","India")
    email=st.checkbox("Email report after run")
    if st.button("Run discovery",type="primary"):
        with st.spinner("Running discovery and ranking..."):
            result=post("/agent/run",{"source":source,"what":query,"where":location,"send_email":email})
        if result: st.success("Run completed"); st.json(result)
    if st.button("Send email report"):
        result=post("/reports/email")
        if result: st.json(result)

health=get("/health") or {}
metrics=get("/metrics") or {}
a,b,c,d=st.columns(4)
a.metric("Jobs",metrics.get("jobs_total",0)); b.metric("Pending approvals",metrics.get("approvals_pending",0))
c.metric("Submitted",metrics.get("applications_submitted",0))
d.metric("Scheduler","Enabled" if health.get("scheduler_enabled") else "Manual")
jobs_tab, approvals_tab, events_tab=st.tabs(["Ranked jobs","Approval queue","Monitoring"])
with jobs_tab:
    minimum=st.slider("Minimum score",0,100,0)
    jobs=get("/jobs",{"limit":100,"min_score":minimum}) or []
    for job in jobs:
        with st.expander(f"{job['score']:.0f}/100 · {job['title']} — {job['company']}"):
            st.write(job.get("score_reasons",[])); st.write(job.get("description",""))
            st.markdown(f"[Open job posting]({job['url']})")
            if st.button("Queue for approval",key="queue_"+job["id"]):
                result=post("/approvals/"+job["id"])
                if result: st.success(result["message"]); st.rerun()
with approvals_tab:
    pending=get("/approvals",{"status":"pending"}) or []
    if not pending: st.info("No pending approvals.")
    for item in pending:
        with st.expander(f"{item['title']} — {item['company']} ({item['score']:.0f}/100)"):
            st.markdown(f"[Open job posting]({item['url']})")
            notes=st.text_input("Notes",key="notes_"+item["job_id"])
            left,right=st.columns(2)
            if left.button("Approve",key="yes_"+item["job_id"]):
                if post(f"/approvals/{item['job_id']}/decision",body={"decision":"approved","notes":notes}):
                    st.success("Approved. Complete the application manually on the employer site."); st.rerun()
            if right.button("Reject",key="no_"+item["job_id"]):
                if post(f"/approvals/{item['job_id']}/decision",body={"decision":"rejected","notes":notes}):
                    st.success("Rejected."); st.rerun()
with events_tab:
    st.write("Health"); st.json(health)
    st.write("Metrics"); st.json(metrics)
    st.write("Recent events"); st.json(get("/events",{"limit":100}) or [])
