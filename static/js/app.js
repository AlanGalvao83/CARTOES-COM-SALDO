const state = {
  user: null,
  currentCard: null,
  lastMovimentacao: null,
  duplicateCards: [],
};

// Utilitários de formatação
function formatBRL(val) {
  const num = Number(val || 0);
  return num.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function parseMoneyInput(str) {
  if (!str) return 0;
  const clean = String(str)
    .replace("R$", "")
    .replace(/\s/g, "")
    .replace(/\./g, "")
    .replace(",", ".");
  const n = parseFloat(clean);
  return isNaN(n) ? 0 : Math.round(n * 100) / 100;
}

function formatDateBR(dateStr) {
  if (!dateStr) return "—";
  if (dateStr.length === 10 && dateStr.includes("-")) {
    const [y, m, d] = dateStr.split("-");
    return `${d}/${m}/${y}`;
  }
  if (dateStr.length >= 16 && dateStr.includes("-")) {
    const [datePart, timePart] = dateStr.split(" ");
    const [y, m, d] = datePart.split("-");
    return `${d}/${m}/${y} ${timePart.slice(0, 5)}`;
  }
  return dateStr;
}

function showToast(msg, type = "info") {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 4000);
}

async function apiFetch(url, options = {}) {
  const opts = {
    headers: { "Content-Type": "application/json" },
    ...options,
  };
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || "Erro na requisição");
  }
  return data;
}

// ==================== AUTENTICAÇÃO ====================
async function checkAuth() {
  try {
    const data = await apiFetch("/api/auth/me");
    if (data.authenticated) {
      state.user = data.user;
      enterApp();
    } else {
      showLoginScreen(data.available_users || []);
    }
  } catch (err) {
    showLoginScreen([]);
  }
}

function showLoginScreen(users) {
  document.getElementById("login-screen").classList.remove("hidden");
  document.getElementById("app-screen").classList.add("hidden");

  const chipsContainer = document.getElementById("login-user-chips");
  chipsContainer.innerHTML = "";
  users.forEach((u) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "user-chip";
    btn.textContent = `${u.nome} (${u.username})`;
    btn.addEventListener("click", () => {
      document.querySelectorAll(".user-chip").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("login-username").value = u.username;
      const passInput = document.getElementById("login-password");
      passInput.value = "";
      passInput.focus();
    });
    chipsContainer.appendChild(btn);
  });
}

function enterApp() {
  document.getElementById("login-screen").classList.add("hidden");
  document.getElementById("app-screen").classList.remove("hidden");

  document.getElementById("current-user-name").textContent = state.user.nome;
  const roleBadge = document.getElementById("current-user-role");
  if (state.user.perfil === "admin") {
    roleBadge.textContent = "ADMIN";
    roleBadge.classList.add("admin");
    document.getElementById("nav-tab-admin").classList.remove("hidden");
  } else {
    roleBadge.textContent = "CAIXA";
    roleBadge.classList.remove("admin");
    document.getElementById("nav-tab-admin").classList.add("hidden");
  }

  loadResumo();
  loadListaRapida("");
  document.getElementById("input-numero-cartao").focus();
}

// ==================== RESUMO / KPIS ====================
async function loadResumo() {
  try {
    const r = await apiFetch("/api/resumo");
    document.getElementById("kpi-cartoes-ativos").textContent =
      r.cartoes_com_saldo.toLocaleString("pt-BR");
    document.getElementById("kpi-cartoes-zerados").textContent =
      `de ${r.total_cartoes.toLocaleString("pt-BR")} cartões (${r.cartoes_zerados} zerados)`;

    document.getElementById("kpi-saldo-total").textContent = formatBRL(r.saldo_atual_total);
    document.getElementById("kpi-total-utilizado").textContent =
      `Já utilizado: ${formatBRL(r.total_ja_utilizado)}`;

    document.getElementById("kpi-descontado-hoje").textContent = formatBRL(r.descontado_hoje);
    document.getElementById("kpi-atendimentos-hoje").textContent =
      `${r.atendimentos_hoje} baixa(s) realizada(s) hoje`;

    document.getElementById("kpi-meu-descontado").textContent = formatBRL(r.meu_descontado_hoje);
    document.getElementById("kpi-meu-complemento").textContent =
      `Compl. cobrado no caixa: ${formatBRL(r.meu_complemento_hoje)}`;
  } catch (e) {
    console.error(e);
  }
}

// ==================== BUSCA E SELEÇÃO DE CARTÃO ====================
async function loadListaRapida(termo = "") {
  try {
    const data = await apiFetch(
      `/api/cartoes/busca?q=${encodeURIComponent(termo)}&limit=25`
    );
    renderListaRapida(data.cartoes || []);
  } catch (e) {
    showToast(e.message, "error");
  }
}

function renderListaRapida(cartoes) {
  const list = document.getElementById("lista-resultados-rapidos");
  list.innerHTML = "";

  if (!cartoes.length) {
    list.innerHTML = `<div style="text-align:center; padding: 1.5rem; color: var(--text-muted); font-size: 0.9rem;">Nenhum cartão encontrado.</div>`;
    return;
  }

  cartoes.forEach((c) => {
    const item = document.createElement("div");
    item.className = `search-item ${state.currentCard && state.currentCard.id === c.id ? "active" : ""}`;
    const hasBalance = c.saldo > 0;
    const nomeExibicao =
      !c.nome || c.nome === "ATUALIZAÇÃO DE CADASTRO"
        ? "Sem nome cadastrado"
        : c.nome;

    item.innerHTML = `
      <div>
        <div class="search-item-num">Cartão Nº ${c.numero}</div>
        <div class="search-item-name">${nomeExibicao}</div>
      </div>
      <div class="search-item-balance ${hasBalance ? "balance-positive" : "balance-zero"}">
        ${formatBRL(c.saldo)}
      </div>
    `;
    item.addEventListener("click", () => selecionarCartaoPorId(c.id));
    list.appendChild(item);
  });
}

async function buscarCartaoDireto(termo) {
  const limpo = termo.trim();
  if (!limpo) {
    loadListaRapida("");
    return;
  }

  // Se digitou apenas números, tenta buscar direto o número exato primeiro
  if (/^\d+$/.test(limpo)) {
    try {
      const data = await apiFetch(`/api/cartoes/numero/${limpo}`);
      exibirCartaoNoPDV(data.cartao, data.historico, data.duplicados);
      loadListaRapida(limpo);
      return;
    } catch (err) {
      // Se não achou número exato, faz busca aproximada
      const busca = await apiFetch(
        `/api/cartoes/busca?q=${encodeURIComponent(limpo)}&limit=25`
      );
      renderListaRapida(busca.cartoes || []);
      if (busca.cartoes && busca.cartoes.length === 1) {
        selecionarCartaoPorId(busca.cartoes[0].id);
      } else {
        showToast(`Cartão nº ${limpo} não encontrado exatamente. Veja a lista lateral.`, "error");
      }
      return;
    }
  }

  // Busca por nome
  const data = await apiFetch(
    `/api/cartoes/busca?q=${encodeURIComponent(limpo)}&limit=25`
  );
  renderListaRapida(data.cartoes || []);
  if (data.cartoes && data.cartoes.length > 0) {
    selecionarCartaoPorId(data.cartoes[0].id);
  } else {
    showToast("Nenhum cartão encontrado para esse nome.", "error");
  }
}

async function selecionarCartaoPorId(cartaoId, ocultarRecibo = true) {
  try {
    const data = await apiFetch(`/api/cartoes/${cartaoId}`);
    if (ocultarRecibo) {
      document.getElementById("last-receipt-banner").classList.add("hidden");
    }
    exibirCartaoNoPDV(data.cartao, data.historico, data.duplicados);
  } catch (e) {
    showToast(e.message, "error");
  }
}

function exibirCartaoNoPDV(cartao, historico = [], duplicados = []) {
  state.currentCard = cartao;
  state.duplicateCards = duplicados || [];

  document.getElementById("pdv-empty-state").classList.add("hidden");
  document.getElementById("pdv-card-content").classList.remove("hidden");

  // Aviso de duplicados (ex: cartão 54934 na planilha)
  const dupWarning = document.getElementById("pdv-duplicate-warning");
  const dupBtns = document.getElementById("pdv-duplicate-buttons");
  if (duplicados && duplicados.length > 1) {
    dupWarning.classList.remove("hidden");
    dupBtns.innerHTML = "";
    duplicados.forEach((d, idx) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `btn ${d.id === cartao.id ? "btn-primary" : "btn-outline"}`;
      b.style.padding = "0.35rem 0.75rem";
      b.style.fontSize = "0.8rem";
      b.textContent = `Registro #${idx + 1} — Saldo: ${formatBRL(d.saldo)} (Início: ${formatDateBR(d.data_inicio)})`;
      b.addEventListener("click", () => selecionarCartaoPorId(d.id));
      dupBtns.appendChild(b);
    });
  } else {
    dupWarning.classList.add("hidden");
  }

  // Dados principais do cartão
  document.getElementById("card-display-numero").textContent = `Nº ${cartao.numero}`;
  document.getElementById("card-display-grupo").textContent = cartao.grupo;
  document.getElementById("card-display-saldo-inicial").textContent = formatBRL(cartao.saldo_inicial);
  document.getElementById("card-display-datas").textContent =
    `${formatDateBR(cartao.data_inicio)} até ${formatDateBR(cartao.data_fim)}`;
  document.getElementById("card-display-saldo").textContent = formatBRL(cartao.saldo);

  const balanceBox = document.getElementById("card-balance-container");
  if (cartao.saldo > 0) {
    balanceBox.classList.add("has-balance");
    balanceBox.classList.remove("no-balance");
    document.getElementById("box-operacao-baixa").classList.remove("hidden");
    document.getElementById("box-cartao-zerado").classList.add("hidden");
  } else {
    balanceBox.classList.remove("has-balance");
    balanceBox.classList.add("no-balance");
    document.getElementById("box-operacao-baixa").classList.add("hidden");
    document.getElementById("box-cartao-zerado").classList.remove("hidden");
  }

  // Nome / Cadastro
  const inputNome = document.getElementById("input-card-nome");
  const badgeCadastro = document.getElementById("badge-atualizar-cadastro");
  if (cartao.precisa_atualizar_nome) {
    inputNome.value = "";
    inputNome.placeholder = "ATUALIZAÇÃO DE CADASTRO — Digite o nome do cliente";
    badgeCadastro.classList.remove("hidden");
  } else {
    inputNome.value = cartao.nome;
    badgeCadastro.classList.add("hidden");
  }

  // Limpa campos de lançamento e foca no valor da estadia
  const inputValor = document.getElementById("input-valor-estadia");
  inputValor.value = "";
  document.getElementById("input-obs-estadia").value = "";
  document.getElementById("simulation-container").classList.add("hidden");

  renderHistoricoCartao(historico);

  if (cartao.saldo > 0) {
    setTimeout(() => inputValor.focus(), 50);
  }
}

// ==================== SIMULAÇÃO EM TEMPO REAL DA BAIXA ====================
function updateSimulacao() {
  const card = state.currentCard;
  const simBox = document.getElementById("simulation-container");
  if (!card || card.saldo <= 0) {
    simBox.classList.add("hidden");
    return;
  }

  const rawVal = document.getElementById("input-valor-estadia").value;
  const valorEstadia = parseMoneyInput(rawVal);

  if (valorEstadia <= 0) {
    simBox.classList.add("hidden");
    return;
  }

  simBox.classList.remove("hidden");
  const saldoAtual = card.saldo;
  const simTitle = document.getElementById("sim-title");
  const elDesconto = document.getElementById("sim-desconto-cartao");
  const elNovoSaldo = document.getElementById("sim-novo-saldo");
  const elCobrarCaixa = document.getElementById("sim-cobrar-caixa");

  if (valorEstadia <= saldoAtual) {
    const novoSaldo = Math.round((saldoAtual - valorEstadia) * 100) / 100;
    simBox.className = "simulation-box sim-ok";
    simTitle.innerHTML = "✅ Saldo suficiente para cobrir toda a estadia";
    elDesconto.textContent = `- ${formatBRL(valorEstadia)}`;
    elNovoSaldo.textContent = formatBRL(novoSaldo);
    elCobrarCaixa.textContent = "R$ 0,00 (Nada a cobrar)";
    elCobrarCaixa.className = "sim-item-val";
  } else {
    const diferenca = Math.round((valorEstadia - saldoAtual) * 100) / 100;
    simBox.className = "simulation-box sim-partial";
    simTitle.innerHTML =
      "⚠️ SALDO INSUFICIENTE — Baixa Parcial (Zerar cartão e cobrar diferença no caixa)";
    elDesconto.textContent = `- ${formatBRL(saldoAtual)} (Saldo total)`;
    elNovoSaldo.textContent = "R$ 0,00 (Cartão Zerado)";
    elCobrarCaixa.textContent = `${formatBRL(diferenca)} A COBRAR`;
    elCobrarCaixa.className = "sim-item-val sim-highlight-cash";
  }
}

// ==================== CONFIRMAR DESCONTO DE SALDO ====================
async function handleDescontarSaldo(e) {
  e.preventDefault();
  if (!state.currentCard) return;

  const valorEstadia = parseMoneyInput(
    document.getElementById("input-valor-estadia").value
  );
  if (valorEstadia <= 0) {
    showToast("Informe um valor de estadia válido maior que zero.", "error");
    return;
  }

  const obs = document.getElementById("input-obs-estadia").value.trim();
  const nomeCliente = document.getElementById("input-card-nome").value.trim();

  const btn = document.getElementById("btn-confirmar-baixa");
  btn.disabled = true;
  btn.textContent = "Processando baixa...";

  try {
    const res = await apiFetch(
      `/api/cartoes/${state.currentCard.id}/descontar`,
      {
        method: "POST",
        body: JSON.stringify({
          valor_estadia: valorEstadia,
          observacao: obs,
          nome_cliente: nomeCliente,
        }),
      }
    );

    state.lastMovimentacao = res.movimentacao;
    showToast(res.message, "success");

    exibirCartaoNoPDV(res.cartao, res.historico, state.duplicateCards);
    exibirBannerComprovante(res.movimentacao, res.cartao);
    loadResumo();
    loadListaRapida(document.getElementById("input-numero-cartao").value);
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Confirmar Desconto de Saldo";
  }
}

function exibirBannerComprovante(mov, cartao) {
  const banner = document.getElementById("last-receipt-banner");
  const title = document.getElementById("receipt-title");
  const details = document.getElementById("receipt-details");

  banner.classList.remove("hidden");
  if (mov.tipo === "BAIXA_PARCIAL") {
    title.innerHTML = `⚠️ Baixa Parcial Realizada — COBRAR ${formatBRL(mov.valor_complemento_caixa)} NO CAIXA!`;
    title.style.color = "#fbbf24";
  } else {
    title.innerHTML = `✅ Baixa Realizada com Sucesso! (Cartão Nº ${cartao.numero})`;
    title.style.color = "#34d399";
  }

  details.innerHTML = `
    Estadia: <strong>${formatBRL(mov.valor_estadia)}</strong> |
    Descontado do Cartão: <strong>${formatBRL(mov.valor_descontado)}</strong> |
    Saldo Remanescente: <strong>${formatBRL(mov.saldo_posterior)}</strong> |
    Cobrar no Caixa: <strong style="color: ${mov.valor_complemento_caixa > 0 ? "#fbbf24" : "#34d399"};">${formatBRL(mov.valor_complemento_caixa)}</strong>
  `;
}

function imprimirComprovante() {
  const mov = state.lastMovimentacao;
  const card = state.currentCard;
  if (!mov || !card) return;

  const win = window.open("", "_blank", "width=380,height=520");
  if (!win) return;
  win.document.write(`
    <html>
    <head>
      <title>Comprovante Cartão Nº ${card.numero}</title>
      <style>
        body { font-family: monospace; padding: 16px; max-width: 300px; margin: 0 auto; color: #000; }
        h2, h3 { text-align: center; margin: 4px 0; }
        .line { border-top: 1px dashed #000; margin: 10px 0; }
        .row { display: flex; justify-content: space-between; margin: 5px 0; font-size: 13px; }
        .big { font-size: 16px; font-weight: bold; }
      </style>
    </head>
    <body onload="window.print();">
      <h2>ESTACIONAMENTO</h2>
      <h3>COMPROVANTE DE SALDO</h3>
      <div class="line"></div>
      <div class="row"><span>Data/Hora:</span><span>${formatDateBR(mov.criado_em)}</span></div>
      <div class="row"><span>Operadora:</span><span>${mov.usuario_nome}</span></div>
      <div class="row big"><span>Cartão Nº:</span><span>${card.numero}</span></div>
      <div class="line"></div>
      <div class="row"><span>Saldo Anterior:</span><span>${formatBRL(mov.saldo_anterior)}</span></div>
      <div class="row"><span>Valor da Estadia:</span><span>${formatBRL(mov.valor_estadia)}</span></div>
      <div class="row big"><span>Descontado Cartão:</span><span>- ${formatBRL(mov.valor_descontado)}</span></div>
      <div class="row big"><span>SALDO ATUAL:</span><span>${formatBRL(mov.saldo_posterior)}</span></div>
      ${
        mov.valor_complemento_caixa > 0
          ? `<div class="line"></div><div class="row big"><span>DIFERENÇA PAGA CAIXA:</span><span>${formatBRL(mov.valor_complemento_caixa)}</span></div>`
          : ""
      }
      <div class="line"></div>
      <div style="text-align:center; font-size:11px; margin-top:10px;">Obrigado pela preferência!</div>
    </body>
    </html>
  `);
  win.document.close();
}

// ==================== HISTÓRICO DO CARTÃO E ESTORNO ====================
function renderHistoricoCartao(historico) {
  const tbody = document.getElementById("tbody-card-history");
  tbody.innerHTML = "";

  if (!historico || !historico.length) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color: var(--text-muted); padding: 1.25rem;">Nenhuma utilização registrada para este cartão até o momento.</td></tr>`;
    return;
  }

  historico.forEach((m) => {
    const tr = document.createElement("tr");
    if (m.estornado) tr.className = "row-estornado";

    let badgeTipo = `<span class="badge badge-total">Baixa Total</span>`;
    if (m.tipo === "BAIXA_PARCIAL") {
      badgeTipo = `<span class="badge badge-parcial">Baixa Parcial</span>`;
    } else if (m.tipo === "ESTORNO") {
      badgeTipo = `<span class="badge badge-estorno">Estorno (+Saldo)</span>`;
    }

    const podeEstornar = !m.estornado && m.tipo !== "ESTORNO";

    tr.innerHTML = `
      <td>${formatDateBR(m.criado_em)}</td>
      <td>${m.usuario_nome}</td>
      <td>${badgeTipo}</td>
      <td>${formatBRL(m.valor_estadia)}</td>
      <td style="font-weight: 700; color: ${m.tipo === "ESTORNO" ? "#34d399" : "#38bdf8"};">
        ${m.tipo === "ESTORNO" ? "+" : "-"} ${formatBRL(m.valor_descontado)}
      </td>
      <td style="color: ${m.valor_complemento_caixa > 0 ? "#fbbf24" : "var(--text-muted)"}; font-weight: 600;">
        ${formatBRL(m.valor_complemento_caixa)}
      </td>
      <td style="font-weight: 700;">${formatBRL(m.saldo_posterior)}</td>
      <td>${m.observacao || "—"}</td>
      <td>
        ${
          podeEstornar
            ? `<button type="button" class="btn btn-danger-outline" data-estornar="${m.id}">Estornar</button>`
            : m.estornado
            ? `<span style="font-size:0.75rem; color:#f87171;">Estornado</span>`
            : "—"
        }
      </td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll("[data-estornar]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const movId = btn.getAttribute("data-estornar");
      estornarMovimentacao(movId);
    });
  });
}

async function estornarMovimentacao(movId, recarregarGeral = false) {
  const motivo = prompt(
    "Confirma o ESTORNO deste lançamento? O valor descontado será devolvido ao saldo do cartão.\n\nInforme o motivo do estorno:",
    "Erro de digitação no caixa"
  );
  if (motivo === null) return;

  try {
    const res = await apiFetch(`/api/movimentacoes/${movId}/estornar`, {
      method: "POST",
      body: JSON.stringify({ motivo }),
    });
    showToast(res.message, "success");
    if (state.currentCard && res.cartao.id === state.currentCard.id) {
      exibirCartaoNoPDV(res.cartao, res.historico, state.duplicateCards);
      document.getElementById("last-receipt-banner").classList.add("hidden");
    }
    loadResumo();
    if (recarregarGeral) {
      carregarHistoricoGeral();
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ==================== HISTÓRICO GERAL (ABA 2) ====================
async function carregarHistoricoGeral() {
  const dtInicio = document.getElementById("filtro-mov-inicio").value;
  const dtFim = document.getElementById("filtro-mov-fim").value;
  const cartao = document.getElementById("filtro-mov-cartao").value.trim();

  const params = new URLSearchParams();
  if (dtInicio) params.set("data_inicio", dtInicio);
  if (dtFim) params.set("data_fim", dtFim);
  if (cartao) params.set("cartao", cartao);

  try {
    const data = await apiFetch(`/api/movimentacoes?${params.toString()}`);
    const tbody = document.getElementById("tbody-historico-geral");
    tbody.innerHTML = "";

    if (!data.movimentacoes.length) {
      tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding: 1.5rem; color: var(--text-muted);">Nenhuma movimentação encontrada para os filtros selecionados.</td></tr>`;
      return;
    }

    data.movimentacoes.forEach((m) => {
      const tr = document.createElement("tr");
      if (m.estornado) tr.className = "row-estornado";

      let badgeTipo = `<span class="badge badge-total">Baixa Total</span>`;
      if (m.tipo === "BAIXA_PARCIAL") {
        badgeTipo = `<span class="badge badge-parcial">Baixa Parcial</span>`;
      } else if (m.tipo === "ESTORNO") {
        badgeTipo = `<span class="badge badge-estorno">Estorno</span>`;
      }

      const podeEstornar = !m.estornado && m.tipo !== "ESTORNO";

      tr.innerHTML = `
        <td>#${m.id}</td>
        <td>${formatDateBR(m.criado_em)}</td>
        <td><strong style="color:#38bdf8; cursor:pointer;" data-abrir-cartao="${m.cartao_id}">Nº ${m.cartao_numero}</strong></td>
        <td>${m.usuario_nome}</td>
        <td>${badgeTipo}</td>
        <td>${formatBRL(m.valor_estadia)}</td>
        <td style="font-weight:700;">${formatBRL(m.valor_descontado)}</td>
        <td style="color:${m.valor_complemento_caixa > 0 ? "#fbbf24" : "inherit"}; font-weight:600;">${formatBRL(m.valor_complemento_caixa)}</td>
        <td>${formatBRL(m.saldo_anterior)} → <strong>${formatBRL(m.saldo_posterior)}</strong></td>
        <td>${m.observacao || "—"}</td>
        <td>
          ${
            podeEstornar
              ? `<button type="button" class="btn btn-danger-outline" data-estornar-geral="${m.id}">Estornar</button>`
              : m.estornado
              ? `<span style="font-size:0.75rem; color:#f87171;">Estornado</span>`
              : "—"
          }
        </td>
      `;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll("[data-estornar-geral]").forEach((b) => {
      b.addEventListener("click", () =>
        estornarMovimentacao(b.getAttribute("data-estornar-geral"), true)
      );
    });

    tbody.querySelectorAll("[data-abrir-cartao]").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-abrir-cartao");
        switchTab("tab-pdv");
        selecionarCartaoPorId(id);
      });
    });
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ==================== BASE DE CARTÕES (ABA 3) ====================
async function carregarBaseCartoes() {
  const termo = document.getElementById("filtro-base-termo").value.trim();
  const saldo = document.getElementById("filtro-base-saldo").value;

  try {
    const data = await apiFetch(
      `/api/cartoes/busca?q=${encodeURIComponent(termo)}&saldo=${encodeURIComponent(saldo)}&limit=80`
    );
    const tbody = document.getElementById("tbody-base-cartoes");
    tbody.innerHTML = "";

    if (!data.cartoes.length) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 1.5rem; color: var(--text-muted);">Nenhum cartão encontrado.</td></tr>`;
      return;
    }

    data.cartoes.forEach((c) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong style="color:#38bdf8; font-size:1rem;">Nº ${c.numero}</strong></td>
        <td>${c.grupo}</td>
        <td>${c.nome || '<span style="color:var(--text-muted)">Não informado</span>'}</td>
        <td>${formatBRL(c.saldo_inicial)}</td>
        <td>
          <span class="search-item-balance ${c.saldo > 0 ? "balance-positive" : "balance-zero"}">
            ${formatBRL(c.saldo)}
          </span>
        </td>
        <td>${formatDateBR(c.data_inicio)}</td>
        <td>${formatDateBR(c.data_fim)}</td>
        <td>
          <button type="button" class="btn btn-primary" style="padding: 0.35rem 0.75rem; font-size: 0.8rem;" data-atender="${c.id}">
            Abrir no Caixa
          </button>
        </td>
      `;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll("[data-atender]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-atender");
        switchTab("tab-pdv");
        selecionarCartaoPorId(id);
      });
    });
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ==================== ADMINISTRAÇÃO (ABA 4) ====================
async function carregarUsuariosAdmin() {
  try {
    const data = await apiFetch("/api/usuarios");
    const tbody = document.getElementById("tbody-usuarios");
    tbody.innerHTML = "";
    data.usuarios.forEach((u) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><code>${u.username}</code></td>
        <td>${u.nome}</td>
        <td><span class="role-pill ${u.perfil === "admin" ? "admin" : ""}">${u.perfil}</span></td>
        <td>${u.ativo ? "Ativo" : "Inativo"}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error(err);
  }
}

// ==================== NAVEGAÇÃO ENTRE ABAS ====================
function switchTab(tabId) {
  document.querySelectorAll(".nav-tab").forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-tab") === tabId);
  });
  document.querySelectorAll(".tab-content").forEach((sec) => {
    sec.classList.toggle("hidden", sec.id !== tabId);
  });

  if (tabId === "tab-pdv") {
    document.getElementById("input-numero-cartao").focus();
  } else if (tabId === "tab-historico") {
    carregarHistoricoGeral();
  } else if (tabId === "tab-cartoes") {
    carregarBaseCartoes();
  } else if (tabId === "tab-admin") {
    carregarUsuariosAdmin();
  }
}

// ==================== EVENT LISTENERS ====================
document.addEventListener("DOMContentLoaded", () => {
  checkAuth();

  // Login
  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("login-username").value.trim();
    const password = document.getElementById("login-password").value;
    try {
      const data = await apiFetch("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      state.user = data.user;
      enterApp();
      showToast(`Bem-vindo(a), ${data.user.nome}!`, "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  // Logout
  document.getElementById("btn-logout").addEventListener("click", async () => {
    await apiFetch("/api/auth/logout", { method: "POST" });
    state.user = null;
    state.currentCard = null;
    checkAuth();
  });

  // Abas
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.getAttribute("data-tab")));
  });

  // Busca Rápida no PDV
  const inputBusca = document.getElementById("input-numero-cartao");
  document.getElementById("form-busca-rapida").addEventListener("submit", (e) => {
    e.preventDefault();
    buscarCartaoDireto(inputBusca.value);
  });

  inputBusca.addEventListener("input", () => {
    loadListaRapida(inputBusca.value);
  });

  document.getElementById("btn-limpar-busca").addEventListener("click", () => {
    inputBusca.value = "";
    loadListaRapida("");
    inputBusca.focus();
  });

  // Atalho global F2 para focar imediatamente na busca de cartão
  document.addEventListener("keydown", (e) => {
    if (e.key === "F2") {
      e.preventDefault();
      switchTab("tab-pdv");
      inputBusca.focus();
      inputBusca.select();
    }
  });

  // Botões de valores rápidos da estadia
  document.querySelectorAll(".quick-btn[data-val]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const inputVal = document.getElementById("input-valor-estadia");
      inputVal.value = btn.getAttribute("data-val");
      updateSimulacao();
      inputVal.focus();
    });
  });

  document.getElementById("btn-usar-saldo-total").addEventListener("click", () => {
    if (!state.currentCard) return;
    const inputVal = document.getElementById("input-valor-estadia");
    inputVal.value = state.currentCard.saldo.toFixed(2).replace(".", ",");
    updateSimulacao();
    inputVal.focus();
  });

  document
    .getElementById("input-valor-estadia")
    .addEventListener("input", updateSimulacao);

  // Salvar Nome do Cliente
  document.getElementById("btn-salvar-nome").addEventListener("click", async () => {
    if (!state.currentCard) return;
    const nome = document.getElementById("input-card-nome").value.trim();
    if (!nome) {
      showToast("Digite o nome do cliente para salvar.", "error");
      return;
    }
    try {
      const res = await apiFetch(`/api/cartoes/${state.currentCard.id}/cadastro`, {
        method: "PUT",
        body: JSON.stringify({ nome }),
      });
      state.currentCard = res.cartao;
      document.getElementById("badge-atualizar-cadastro").classList.add("hidden");
      showToast(res.message, "success");
      loadListaRapida(inputBusca.value);
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  // Confirmar Baixa
  document
    .getElementById("form-baixa-estadia")
    .addEventListener("submit", handleDescontarSaldo);

  // Botões do comprovante
  document
    .getElementById("btn-print-receipt")
    .addEventListener("click", imprimirComprovante);

  document
    .getElementById("btn-novo-atendimento")
    .addEventListener("click", () => {
      document.getElementById("last-receipt-banner").classList.add("hidden");
      inputBusca.value = "";
      inputBusca.focus();
    });

  // Filtro de Histórico Geral
  document
    .getElementById("form-filtro-historico")
    .addEventListener("submit", (e) => {
      e.preventDefault();
      carregarHistoricoGeral();
    });

  document
    .getElementById("btn-exportar-movimentacoes")
    .addEventListener("click", () => {
      const dtInicio = document.getElementById("filtro-mov-inicio").value;
      const dtFim = document.getElementById("filtro-mov-fim").value;
      const params = new URLSearchParams();
      if (dtInicio) params.set("data_inicio", dtInicio);
      if (dtFim) params.set("data_fim", dtFim);
      window.location.href = `/api/exportar/movimentacoes?${params.toString()}`;
    });

  // Filtro Base de Cartões
  document
    .getElementById("form-filtro-cartoes")
    .addEventListener("submit", (e) => {
      e.preventDefault();
      carregarBaseCartoes();
    });

  // Admin: Nova Operadora
  document
    .getElementById("form-novo-usuario")
    .addEventListener("submit", async (e) => {
      e.preventDefault();
      const username = document.getElementById("novo-user-login").value.trim();
      const nome = document.getElementById("novo-user-nome").value.trim();
      const senha = document.getElementById("novo-user-senha").value;
      const perfil = document.getElementById("novo-user-perfil").value;
      try {
        const res = await apiFetch("/api/usuarios", {
          method: "POST",
          body: JSON.stringify({ username, nome, senha, perfil }),
        });
        showToast(res.message, "success");
        e.target.reset();
        carregarUsuariosAdmin();
      } catch (err) {
        showToast(err.message, "error");
      }
    });

  // Admin: Novo Cartão
  document
    .getElementById("form-novo-cartao")
    .addEventListener("submit", async (e) => {
      e.preventDefault();
      const numero = document.getElementById("novo-cartao-numero").value;
      const grupo = document.getElementById("novo-cartao-grupo").value;
      const saldo = document.getElementById("novo-cartao-saldo").value;
      const nome = document.getElementById("novo-cartao-nome").value;
      try {
        const res = await apiFetch("/api/cartoes/novo", {
          method: "POST",
          body: JSON.stringify({ numero, grupo, saldo, nome }),
        });
        showToast(res.message, "success");
        e.target.reset();
        loadResumo();
      } catch (err) {
        showToast(err.message, "error");
      }
    });
});

