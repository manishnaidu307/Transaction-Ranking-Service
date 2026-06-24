/* global fetch */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const txnForm        = $("txnForm");
  const summaryForm     = $("summaryForm");
  const tapeFeed        = $("tapeFeed");
  const connStatus      = $("connStatus");
  const txnResult       = $("txnResult");
  const summaryResult   = $("summaryResult");
  const rankingBody     = $("rankingBody");

  let lastPayload = null; 

  // Helpers

  function genKey() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "key-" + Math.random().toString(36).slice(2) + Date.now();
  }

  function fmtMoney(n) {
    return Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString();
  }

  async function postTransaction(payload) {
    const res = await fetch("/transaction", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let body = null;
    try { body = await res.json(); } catch (_e) {  }
    return { status: res.status, body };
  }

  async function getJSON(url) {
    const res = await fetch(url);
    let body = null;
    try { body = await res.json(); } catch (_e) {  }
    return { status: res.status, body };
  }

  function addTapeEntry(html) {
    const placeholder = tapeFeed.querySelector(".tape__entry--placeholder");
    if (placeholder) placeholder.remove();
    const el = document.createElement("div");
    el.className = "tape__entry";
    el.innerHTML = html;
    tapeFeed.prepend(el);
    while (tapeFeed.children.length > 25) {
      tapeFeed.removeChild(tapeFeed.lastChild);
    }
    tapeFeed.scrollLeft = 0;
  }

  function logResultToTape(payload, status, body) {
    const time = new Date().toLocaleTimeString();
    if (status === 201) {
      addTapeEntry(
        `<span class="t-amt">+₹${fmtMoney(payload.amount)}</span> &rarr; ` +
        `<span class="t-user">${payload.user_id}</span> ` +
        `<span class="muted">(${time})</span>`
      );
    } else if (status === 200 && body && body.duplicate) {
      addTapeEntry(
        `<span class="t-dup">duplicate caught</span> &mdash; ` +
        `<span class="t-user">${payload.user_id}</span> retried, not double-counted ` +
        `<span class="muted">(${time})</span>`
      );
    } else if (status === 429) {
      addTapeEntry(
        `<span class="t-flag">rate limited</span> &mdash; ` +
        `<span class="t-user">${payload.user_id}</span> sent too fast ` +
        `<span class="muted">(${time})</span>`
      );
    } else if (status === 409) {
      addTapeEntry(
        `<span class="t-flag">409 conflict</span> &mdash; key reused with a different payload ` +
        `<span class="muted">(${time})</span>`
      );
    } else {
      addTapeEntry(
        `<span class="t-flag">error ${status}</span> &mdash; <span class="t-user">${payload.user_id}</span> ` +
        `<span class="muted">(${time})</span>`
      );
    }
  }

  function renderTxnResult(status, body) {
    if (status === 201) {
      txnResult.innerHTML = `
        <div class="note note--ok">
          <div class="note__title">Posted</div>
          Transaction #${body.transaction_id} recorded for <strong>${body.user_id}</strong>.
          <dl class="kv">
            <dt>New total</dt><dd>₹${fmtMoney(body.user_summary.total_amount)}</dd>
            <dt>Transactions</dt><dd>${body.user_summary.transaction_count}</dd>
            <dt>Ranking score</dt><dd>${body.user_summary.ranking_score}</dd>
          </dl>
        </div>`;
    } else if (status === 200 && body && body.duplicate) {
      txnResult.innerHTML = `
        <div class="note note--dup">
          <div class="note__title">Duplicate caught</div>
          Same idempotency key seen before &mdash; original result returned, nothing was processed twice.
          <dl class="kv">
            <dt>Transaction #</dt><dd>${body.transaction_id}</dd>
            <dt>Total stays</dt><dd>₹${fmtMoney(body.user_summary.total_amount)}</dd>
          </dl>
        </div>`;
    } else if (status === 409) {
      txnResult.innerHTML = `
        <div class="note note--error">
          <div class="note__title">409 &mdash; Idempotency conflict</div>
          ${body && body.message ? body.message : "This key was already used with a different payload."}
        </div>`;
    } else if (status === 429) {
      txnResult.innerHTML = `
        <div class="note note--error">
          <div class="note__title">429 &mdash; Rate limited</div>
          ${body && body.message ? body.message : "Too many requests for this user. Wait a moment and try again."}
        </div>`;
    } else if (status === 422) {
      txnResult.innerHTML = `
        <div class="note note--error">
          <div class="note__title">422 &mdash; Invalid request</div>
          ${body && body.message ? body.message : "Check the form fields and try again."}
        </div>`;
    } else {
      txnResult.innerHTML = `
        <div class="note note--error">
          <div class="note__title">Error ${status}</div>
          ${body && body.message ? body.message : "Something went wrong."}
        </div>`;
    }
  }

  // Submit transaction

  function currentPayload() {
    return {
      user_id: $("f_user_id").value.trim(),
      amount: parseFloat($("f_amount").value),
      description: $("f_description").value.trim() || null,
      idempotency_key: $("f_idem_key").value.trim(),
    };
  }

  async function submitPayload(payload) {
    const { status, body } = await postTransaction(payload);
    renderTxnResult(status, body);
    logResultToTape(payload, status, body);
    if (status === 201 || (status === 200 && body && body.duplicate)) {
      refreshRanking();
    }
    return { status, body };
  }

  $("f_idem_key").value = genKey();

  $("regenKeyBtn").addEventListener("click", () => {
    $("f_idem_key").value = genKey();
  });

  txnForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = currentPayload();
    if (!payload.user_id || !payload.amount) return;
    lastPayload = payload;
    $("submitBtn").disabled = true;
    try {
      await submitPayload(payload);
    } finally {
      $("submitBtn").disabled = false;
      $("f_idem_key").value = genKey(); // next submission needs a fresh key
    }
  });

  $("repeatBtn").addEventListener("click", async () => {
    if (!lastPayload) {
      txnResult.innerHTML = `<div class="note note--error"><div class="note__title">Nothing to resend</div>Post a transaction first.</div>`;
      return;
    }
    await submitPayload(lastPayload);
  });

  $("burstBtn").addEventListener("click", async () => {
    const userId = $("f_user_id").value.trim() || "burst_demo_user";
    txnResult.innerHTML = `<div class="note note--dup"><div class="note__title">Firing burst</div>Sending 8 rapid requests for <strong>${userId}</strong>&hellip;</div>`;
    const requests = Array.from({ length: 8 }, (_, i) =>
      postTransaction({
        user_id: userId,
        amount: 10 + i,
        idempotency_key: genKey(),
      }).then(({ status, body }) => {
        logResultToTape({ user_id: userId, amount: 10 + i }, status, body);
        return status;
      })
    );
    const results = await Promise.all(requests);
    const accepted = results.filter((s) => s === 201).length;
    const limited = results.filter((s) => s === 429).length;
    txnResult.innerHTML = `
      <div class="note note--dup">
        <div class="note__title">Burst complete</div>
        ${accepted} accepted, ${limited} rate-limited (429) out of 8 &mdash; the per-user limit held even under a burst.
      </div>`;
    refreshRanking();
  });

  // Summary lookup

  function renderSummary(status, body, userId) {
    if (status === 200) {
      summaryResult.innerHTML = `
        <div class="stat-row"><span class="stat-label">Rank</span><span class="stat-rank">#${body.rank} of ${body.total_ranked_users}</span></div>
        <div class="stat-row"><span class="stat-label">Ranking score</span><span>${body.ranking_score}</span></div>
        <div class="stat-row"><span class="stat-label">Total amount</span><span>₹${fmtMoney(body.total_amount)}</span></div>
        <div class="stat-row"><span class="stat-label">Transactions</span><span>${body.transaction_count}</span></div>
        <div class="stat-row"><span class="stat-label">Average amount</span><span>₹${fmtMoney(body.average_transaction_amount)}</span></div>
        <div class="stat-row"><span class="stat-label">Active days</span><span>${body.active_days_count}</span></div>
        <div class="stat-row"><span class="stat-label">First transaction</span><span>${fmtTime(body.first_transaction_at)}</span></div>
        <div class="stat-row"><span class="stat-label">Last transaction</span><span>${fmtTime(body.last_transaction_at)}</span></div>
      `;
    } else if (status === 404) {
      summaryResult.innerHTML = `<p class="muted">No transactions found for <strong>${userId}</strong> yet.</p>`;
    } else {
      summaryResult.innerHTML = `<div class="note note--error"><div class="note__title">Error ${status}</div>${body && body.message ? body.message : "Lookup failed."}</div>`;
    }
  }

  summaryForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const userId = $("s_user_id").value.trim();
    if (!userId) return;
    summaryResult.innerHTML = `<p class="muted">Looking up&hellip;</p>`;
    const { status, body } = await getJSON(`/summary/${encodeURIComponent(userId)}`);
    renderSummary(status, body, userId);
  });


  // Ranking table

  async function refreshRanking() {
    const { status, body } = await getJSON("/ranking?limit=20");
    if (status !== 200 || !body) {
      rankingBody.innerHTML = `<tr><td colspan="6" class="muted">Could not load ranking.</td></tr>`;
      return;
    }
    if (!body.rankings.length) {
      rankingBody.innerHTML = `<tr><td colspan="6" class="muted">No transactions yet. Post one above to appear here.</td></tr>`;
      return;
    }
    rankingBody.innerHTML = body.rankings
      .map(
        (r) => `
        <tr class="rank-${r.rank}">
          <td class="rank-cell">#${r.rank}</td>
          <td class="user-cell">${r.user_id}</td>
          <td>${r.ranking_score}</td>
          <td>₹${fmtMoney(r.total_amount)}</td>
          <td>${r.transaction_count}</td>
          <td>${r.active_days_count}</td>
        </tr>`
      )
      .join("");
  }

  $("refreshRankingBtn").addEventListener("click", refreshRanking);

  // Health check + initial load

  async function checkHealth() {
    try {
      const { status } = await getJSON("/health");
      if (status === 200) {
        connStatus.textContent = "service online";
        connStatus.dataset.state = "ok";
      } else {
        throw new Error("bad status");
      }
    } catch (_e) {
      connStatus.textContent = "service unreachable";
      connStatus.dataset.state = "error";
    }
  }

  checkHealth();
  refreshRanking();
  setInterval(refreshRanking, 15000);
})();
