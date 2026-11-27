/* Mentor do Fiscal — tela do fiscal.
 * Painel: chama /api/dados/* (mesmas funções das ferramentas MCP, sem IA).
 * Chat: fala com o agente do Google ADK por /api/agente/run_sse (streaming).
 * Sem bibliotecas externas: o Markdown do agente é convertido aqui, escapando todo HTML. */
'use strict';

const APP = 'mentor_fiscal';
const USUARIO = 'fiscal';
const API_DADOS = '/api/dados';
const API_AGENTE = '/api/agente';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const memoria = {
  ler(chave, padrao = null) {
    try { const v = localStorage.getItem(chave); return v === null ? padrao : JSON.parse(v); } catch { return padrao; }
  },
  gravar(chave, valor) { try { localStorage.setItem(chave, JSON.stringify(valor)); } catch { /* sem armazenamento */ } },
  apagar(chave) { try { localStorage.removeItem(chave); } catch { /* idem */ } },
};

const estado = {
  om: null,
  dataRef: '31/10/2026',
  situacao: 'vigentes',
  busca: '',
  sessao: null,      // {id, contexto}
  ocupado: false,
  ultima: null,      // última pergunta (para "tentar de novo")
};

/* ------------------------------------------------------------------ utilidades */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const MINUSCULAS = new Set(['de', 'da', 'do', 'das', 'dos', 'e', 'em', 'a', 'o', 'para', 'no', 'na']);
function titulo(s) {
  if (!s) return '';
  return String(s).toLowerCase().split(/\s+/).map((p, i) => {
    if (i > 0 && MINUSCULAS.has(p)) return p;
    return p.split('/').map((q, j) => (j > 0 && q.length === 2 ? q.toUpperCase() : q.charAt(0).toUpperCase() + q.slice(1))).join('/');
  }).join(' ');
}

function dataBR(iso) {
  if (!iso) return '—';
  const [a, m, d] = String(iso).slice(0, 10).split('-');
  return d && m && a ? `${d}/${m}/${a}` : String(iso);
}

function brl(v) {
  if (v === null || v === undefined || v === '') return '—';
  return Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 2 });
}

function brlCurto(v) {
  const n = Number(v || 0);
  if (n >= 1e9) return `R$ ${(n / 1e9).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} bi`;
  if (n >= 1e6) return `R$ ${(n / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} mi`;
  if (n >= 1e3) return `R$ ${(n / 1e3).toLocaleString('pt-BR', { maximumFractionDigits: 0 })} mil`;
  return brl(n);
}

function faixa(janela) {
  const n = String(janela || '').charAt(0);
  const rotulo = String(janela || '').replace(/^\d\.\s*/, '') || '—';
  const classe = { 1: 'vermelho', 2: 'ambar', 3: 'verde', 4: 'azul', 5: 'cinza', 0: 'cinza' }[n] || 'cinza';
  return { rotulo, classe };
}

function prazoTexto(dias) {
  if (dias === null || dias === undefined) return 'sem data de fim';
  if (dias === 0) return 'vence na data de corte';
  if (dias > 0) return `vence em ${dias} ${dias === 1 ? 'dia' : 'dias'}`;
  const d = Math.abs(dias);
  return `encerrou há ${d} ${d === 1 ? 'dia' : 'dias'}`;
}

let avisoTempo;
function avisar(texto) {
  const el = $('#aviso');
  el.textContent = texto;
  el.hidden = false;
  clearTimeout(avisoTempo);
  avisoTempo = setTimeout(() => { el.hidden = true; }, 3800);
}

async function dados(caminho, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v !== undefined && v !== null)).toString();
  const r = await fetch(`${API_DADOS}/${caminho}${q ? `?${q}` : ''}`, { headers: { Accept: 'application/json' } });
  const j = await r.json().catch(() => ({ erro: `Resposta inválida do servidor (${r.status}).` }));
  if (!r.ok || j.erro) throw new Error(j.erro || `Erro ${r.status}`);
  return j;
}

function baixar(url) {
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ------------------------------------------------------------------ Markdown seguro */
function mdInline(texto) {
  let s = esc(texto);
  const codigos = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => { codigos.push(c); return `\u0000${codigos.length - 1}\u0000`; });
  s = s.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/(?!\/))[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, (_, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
  s = s.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?!\w)/g, '$1<em>$2</em>');
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codigos[Number(i)]}</code>`);
  return s;
}

function mdCelulas(linha) {
  let l = linha.trim();
  if (l.startsWith('|')) l = l.slice(1);
  if (l.endsWith('|')) l = l.slice(0, -1);
  return l.split('|').map((c) => c.trim());
}

function mdLista(itens) {
  let html = '';
  const pilha = [];
  for (const it of itens) {
    const nivel = Math.min(it.nivel, pilha.length);
    const alvo = nivel + 1;
    if (pilha.length > alvo) {
      while (pilha.length > alvo) html += `</li></${pilha.pop()}>`;
      html += '</li>';
    } else if (pilha.length === alvo) {
      html += '</li>';
    } else {
      while (pilha.length < alvo) { const t = it.ordenada ? 'ol' : 'ul'; html += `<${t}>`; pilha.push(t); }
    }
    html += `<li>${mdInline(it.texto)}`;
  }
  while (pilha.length) html += `</li></${pilha.pop()}>`;
  return html;
}

function md(fonte) {
  const linhas = String(fonte || '').replace(/\r/g, '').split('\n');
  let html = '';
  let par = [];
  const fechaPar = () => { if (par.length) { html += `<p>${par.map(mdInline).join('<br>')}</p>`; par = []; } };
  let i = 0;
  while (i < linhas.length) {
    const l = linhas[i];
    let m;
    if (/^\s*```/.test(l)) {
      fechaPar();
      const cod = [];
      i += 1;
      while (i < linhas.length && !/^\s*```/.test(linhas[i])) { cod.push(linhas[i]); i += 1; }
      i += 1;
      html += `<pre><code>${esc(cod.join('\n'))}</code></pre>`;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < linhas.length && /^\s*\|?\s*:?-{2,}/.test(linhas[i + 1])) {
      fechaPar();
      const cab = mdCelulas(l);
      i += 2;
      const corpo = [];
      while (i < linhas.length && /^\s*\|.*\|\s*$/.test(linhas[i])) { corpo.push(mdCelulas(linhas[i])); i += 1; }
      html += '<div class="tabela-md"><table><thead><tr>' + cab.map((c) => `<th>${mdInline(c)}</th>`).join('') + '</tr></thead><tbody>'
        + corpo.map((r) => '<tr>' + r.map((c) => `<td>${mdInline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>';
      continue;
    }
    if ((m = l.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/))) {
      fechaPar();
      const n = Math.min(m[1].length + 2, 5);
      html += `<h${n}>${mdInline(m[2])}</h${n}>`;
      i += 1;
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      fechaPar();
      const itens = [];
      while (i < linhas.length) {
        const li = linhas[i];
        const mm = li.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
        if (mm) {
          itens.push({ nivel: Math.floor(mm[1].replace(/\t/g, '    ').length / 2), ordenada: /\d/.test(mm[2]), texto: mm[3] });
          i += 1;
        } else if (li.trim() && /^\s{2,}/.test(li) && itens.length) {
          itens[itens.length - 1].texto += ` ${li.trim()}`;
          i += 1;
        } else {
          break;
        }
      }
      html += mdLista(itens);
      continue;
    }
    if (/^\s*>/.test(l)) {
      fechaPar();
      const q = [];
      while (i < linhas.length && /^\s*>/.test(linhas[i])) { q.push(linhas[i].replace(/^\s*>\s?/, '')); i += 1; }
      html += `<blockquote>${q.map(mdInline).join('<br>')}</blockquote>`;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(l)) { fechaPar(); html += '<hr>'; i += 1; continue; }
    if (!l.trim()) { fechaPar(); i += 1; continue; }
    par.push(l.trim());
    i += 1;
  }
  fechaPar();
  return html;
}

/* ------------------------------------------------------------------ telas */
function mostrarInicio() {
  estado.om = null;
  $('#tela-inicio').hidden = false;
  $('#tela-painel').hidden = true;
  $('#topo-om').hidden = true;
  $('#abrir-chat').hidden = true;
  document.body.classList.remove('chat-aberto');
  setTimeout(() => $('#busca-om').focus(), 50);
}

let buscaSeq = 0;
let buscaTempo;
async function buscarOM(q) {
  const seq = ++buscaSeq;
  const lista = $('#resultados-om');
  lista.innerHTML = '<li class="info">Procurando…</li>';
  try {
    const r = await dados('om', { q });
    if (seq !== buscaSeq) return;
    const oms = r.oms_encontradas || [];
    if (!oms.length) { lista.innerHTML = '<li class="info">Nenhuma OM encontrada. Tente outra palavra, a cidade ou o código UASG.</li>'; return; }
    lista.innerHTML = oms.map((o, idx) => `
      <li><button type="button" class="resultado-om" role="option" data-idx="${idx}">
        <span><strong>${esc(titulo(o.nome))}</strong><small>${esc(titulo(o.municipio))}/${esc(o.uf)} · UASG ${esc(o.unidade_codigo)}</small></span>
        <span class="contagem">${Number(o.contratos_vigentes)} vigente${Number(o.contratos_vigentes) === 1 ? '' : 's'}</span>
      </button></li>`).join('');
    $$('.resultado-om', lista).forEach((b) => b.addEventListener('click', () => abrirPainel(oms[Number(b.dataset.idx)])));
  } catch (e) {
    if (seq === buscaSeq) lista.innerHTML = `<li class="info">Não consegui consultar a base: ${esc(e.message)}</li>`;
  }
}

async function abrirPorUasg(uasg) {
  try {
    const r = await dados('om', { q: uasg });
    const om = (r.oms_encontradas || []).find((o) => o.unidade_codigo === uasg) || (r.oms_encontradas || [])[0];
    if (om) abrirPainel(om); else avisar('OM não encontrada na base.');
  } catch (e) { avisar(`Não consegui consultar a base: ${e.message}`); }
}

function abrirPainel(om) {
  estado.om = om;
  estado.situacao = 'vigentes';
  estado.busca = '';
  memoria.gravar('mf:om', om);
  $('#tela-inicio').hidden = true;
  $('#tela-painel').hidden = false;
  $('#topo-om').hidden = false;
  $('#abrir-chat').hidden = false;
  $('#topo-om-nome').textContent = `${titulo(om.nome)} · ${om.unidade_codigo}`;
  $('#om-nome').textContent = titulo(om.nome);
  $('#om-local').textContent = [titulo(om.municipio), om.uf].filter(Boolean).join(' / ');
  $('#om-uasg').textContent = om.unidade_codigo;
  $('#busca-contrato').value = '';
  $$('.aba').forEach((a) => { const ativa = a.dataset.situacao === 'vigentes'; a.classList.toggle('ativa', ativa); a.setAttribute('aria-selected', String(ativa)); });
  window.scrollTo({ top: 0 });
  carregarPainel();
  prepararChat();
}

/* ------------------------------------------------------------------ painel */
async function carregarPainel() {
  const uasg = estado.om.unidade_codigo;
  $('#kpis').innerHTML = Array.from({ length: 5 }, () => '<div class="kpi esqueleto"><span class="esq" style="width:40%;height:28px"></span><span class="esq" style="width:70%;margin-top:8px"></span></div>').join('');
  $('#linhas-carteira').innerHTML = Array.from({ length: 4 }, () => '<tr><td colspan="5"><span class="esq"></span></td></tr>').join('');
  $('#lista-alertas').innerHTML = '<li><span class="esq"></span></li>';

  const [cart, alertas, guia] = await Promise.allSettled([
    dados('carteira', { uasg, situacao: 'vigentes' }),
    dados('alertas', { uasg }),
    dados('guia', { tema: 'primeiros_passos' }),
  ]);
  if (estado.om?.unidade_codigo !== uasg) return;

  if (cart.status === 'fulfilled') {
    estado.dataRef = dataBR(cart.value.data_referencia);
    $('#om-dataref').textContent = estado.dataRef;
    renderTabela(cart.value);
  } else {
    $('#linhas-carteira').innerHTML = `<tr><td class="vazio" colspan="5">Não consegui carregar a carteira: ${esc(cart.reason.message)}</td></tr>`;
  }
  renderKpis(cart.status === 'fulfilled' ? cart.value : null, alertas.status === 'fulfilled' ? alertas.value : null);
  if (alertas.status === 'fulfilled') renderAlertas(alertas.value);
  else $('#lista-alertas').innerHTML = `<li class="vazio">Não consegui carregar os alertas: ${esc(alertas.reason.message)}</li>`;
  renderRoteiro(guia.status === 'fulfilled' ? guia.value.conteudo : '');
}

function renderKpis(cart, alertas) {
  const faixas = {};
  let total = 0;
  let valor = 0;
  for (const r of cart?.resumo_por_faixa || []) {
    faixas[String(r.janela_vencimento).charAt(0)] = Number(r.quantidade);
    total += Number(r.quantidade);
    valor += Number(r.valor_total || 0);
  }
  const contaAlerta = (tipo) => Number((alertas?.contagem_por_tipo || []).find((a) => a.tipo_alerta === tipo)?.quantidade || 0);
  const v90 = faixas['1'] || 0;
  const v180 = faixas['2'] || 0;
  const enc = contaAlerta('Encerrado há até 90 dias');
  const conc = contaAlerta('Fornecedor concentrado na OM');
  const kpis = [
    { valor: total, rotulo: 'Contratos vigentes', det: `${brlCurto(valor)} em valor global`, cls: '', acao: 'vigentes' },
    { valor: v90, rotulo: 'Vencem em até 90 dias', det: v90 ? 'Agir já: avisar o gestor' : 'Nada urgente', cls: v90 ? 'vermelho' : 'zero', acao: 'vigentes' },
    { valor: v180, rotulo: 'Vencem em 91 a 180 dias', det: v180 ? 'Planejar a próxima contratação' : 'Sem vencimentos nessa faixa', cls: v180 ? 'ambar' : 'zero', acao: 'vigentes' },
    { valor: enc, rotulo: 'Encerrados a verificar', det: 'Encerrados há até 90 dias', cls: enc ? 'cinza' : 'zero', acao: 'encerrados_recentes' },
    { valor: conc, rotulo: 'Fornecedor concentrado', det: '40% ou mais do valor da OM', cls: conc ? 'ambar' : 'zero', acao: 'alertas' },
  ];
  $('#kpis').innerHTML = kpis.map((k) => `
    <button type="button" class="kpi ${k.cls}" data-acao="${k.acao}">
      <span class="kpi-valor">${k.valor}</span>
      <span class="kpi-rotulo">${esc(k.rotulo)}</span>
      <span class="kpi-detalhe">${esc(k.det)}</span>
    </button>`).join('');
  $$('#kpis .kpi').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.acao === 'alertas') { $('#alertas').scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    trocarSituacao(b.dataset.acao);
    $('#carteira').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));
}

function renderTabela(c) {
  const corpo = $('#linhas-carteira');
  const contratos = c.contratos || [];
  if (!contratos.length) {
    const msg = estado.busca ? 'Nenhum contrato encontrado com essa busca.'
      : estado.situacao === 'vigentes' ? 'A OM não tem contratos vigentes na base.' : 'Nenhum contrato encerrado nos últimos 90 dias.';
    corpo.innerHTML = `<tr><td class="vazio" colspan="5">${msg}</td></tr>`;
  } else {
    corpo.innerHTML = contratos.map((k, idx) => {
      const f = faixa(k.janela_vencimento);
      return `<tr class="linha" tabindex="0" data-idx="${idx}">
        <td><span class="contrato-num">${esc(k.numero_contrato || '—')}</span><small>${esc(k.tipo_instrumento || '')}</small></td>
        <td><span class="objeto">${esc(k.objeto || '—')}</span><small>${esc(k.fornecedor_nome || '')}</small></td>
        <td class="num">${esc(k.valor_global_fmt || brl(k.valor_global))}</td>
        <td class="data">${esc(k.vigencia_fim_fmt || dataBR(k.vigencia_fim))}<small>${esc(prazoTexto(k.dias_para_vencer))}</small></td>
        <td><span class="selo ${f.classe}">${esc(f.rotulo)}</span></td>
      </tr>`;
    }).join('');
    $$('tr.linha', corpo).forEach((tr) => {
      const k = contratos[Number(tr.dataset.idx)];
      tr.addEventListener('click', () => abrirFicha(k.numero_controle_pncp));
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); abrirFicha(k.numero_controle_pncp); } });
    });
  }
  const nota = [];
  if (c.mostrando >= 40) nota.push('Mostrando os 40 primeiros, ordenados pelo vencimento mais próximo. Use a busca para filtrar.');
  nota.push(`Dias contados a partir de ${estado.dataRef}. Só contratos (empenhos ficam fora da carteira).`);
  $('#nota-carteira').textContent = nota.join(' ');
}

async function carregarTabela() {
  const uasg = estado.om.unidade_codigo;
  $('#linhas-carteira').innerHTML = '<tr><td colspan="5"><span class="esq"></span></td></tr>';
  try {
    const c = await dados('carteira', { uasg, situacao: estado.situacao, busca: estado.busca });
    if (estado.om?.unidade_codigo === uasg) renderTabela(c);
  } catch (e) {
    $('#linhas-carteira').innerHTML = `<tr><td class="vazio" colspan="5">Não consegui carregar: ${esc(e.message)}</td></tr>`;
  }
}

function trocarSituacao(sit) {
  if (sit !== 'vigentes' && sit !== 'encerrados_recentes') return;
  $$('.aba').forEach((a) => { const ativa = a.dataset.situacao === sit; a.classList.toggle('ativa', ativa); a.setAttribute('aria-selected', String(ativa)); });
  if (estado.situacao === sit) return;
  estado.situacao = sit;
  carregarTabela();
}

function renderAlertas(a) {
  const lista = $('#lista-alertas');
  const itens = (a.alertas || []).slice(0, 8);
  if (!itens.length) { lista.innerHTML = '<li class="vazio">Nenhum alerta para esta OM na data de referência.</li>'; return; }
  lista.innerHTML = itens.map((al, idx) => `
    <li class="alerta g${esc(al.gravidade)}">
      <svg><use href="#i-alerta"/></svg>
      <div><strong>${esc(al.tipo_alerta)}</strong><p>${esc(al.mensagem || al.objeto || '')}</p></div>
      ${/^\d{14}-2-\d+\/\d{4}$/.test(al.referencia || '') ? `<button type="button" class="botao secundario pequeno" data-idx="${idx}">Ver ficha</button>` : ''}
    </li>`).join('');
  $$('button[data-idx]', lista).forEach((b) => b.addEventListener('click', () => abrirFicha(itens[Number(b.dataset.idx)].referencia)));
  const total = (a.contagem_por_tipo || []).reduce((s, x) => s + Number(x.quantidade), 0);
  if (total > itens.length) {
    lista.insertAdjacentHTML('beforeend', `<li class="vazio">Mostrando ${itens.length} de ${total} alertas. Peça ao Mentor: “Quais alertas a minha OM tem hoje?”</li>`);
  }
}

/* roteiro da primeira semana (vem do guia do fiscal, o mesmo que o agente usa) */
const ACOES_ROTEIRO = [
  { rotulo: 'Fiscal × gestor', pergunta: 'Qual é a diferença entre o papel do fiscal e o do gestor do contrato? O que eu devo confirmar sobre a minha designação?' },
  { rotulo: 'Ver carteira', rolar: '#carteira' },
  { rotulo: 'O que pedir', pergunta: 'Quais documentos do processo eu devo pedir para os meus contratos e a quem?' },
  { rotulo: 'Roteiro da passagem', pergunta: 'Monte um roteiro de perguntas para a passagem de fiscalização com o fiscal anterior, considerando os contratos da minha OM que vencem primeiro.' },
  { rotulo: 'Como ler', pergunta: 'O que eu devo observar ao ler o contrato e o termo de referência? Use como exemplo o contrato da minha OM que vence primeiro.' },
  { rotulo: 'Exportar agenda', agenda: true },
  { rotulo: 'Mensagem ao preposto', pergunta: 'Escreva uma mensagem curta para eu me apresentar ao preposto da contratada do contrato que vence primeiro na minha OM, informando como as ocorrências serão registradas.' },
];

function lerRoteiro(texto) {
  const passos = [];
  let atual = null;
  for (const linha of String(texto || '').split('\n')) {
    const m = linha.match(/^\s*\d+\.\s+\*\*(.+?)\*\*\s*(.*)$/);
    if (m) { atual = { titulo: m[1], det: m[2] }; passos.push(atual); continue; }
    if (!atual || !linha.trim()) continue;
    const t = linha.trim();
    atual.det += /^[-*]\s/.test(t) ? `\n${t}` : ` ${t}`;
  }
  return passos;
}

function renderRoteiro(texto) {
  const passos = lerRoteiro(texto);
  const ol = $('#roteiro');
  if (!passos.length) { ol.innerHTML = '<li class="vazio">Guia indisponível no momento.</li>'; $('#progresso').innerHTML = ''; return; }
  const chave = `mf:roteiro:${estado.om.unidade_codigo}`;
  const feitos = new Set(memoria.ler(chave, []));
  ol.innerHTML = passos.map((p, i) => {
    const acao = ACOES_ROTEIRO[i];
    // "Apresente-se ao preposto" + "da contratada e..." continua a frase; "Confirme a designação." + "Tenha..." não.
    const continua = /^[a-zà-ú]/.test(p.det.trim());
    const tituloTxt = continua ? p.titulo : p.titulo.replace(/[.:]$/, '');
    return `<li class="passo ${feitos.has(i) ? 'feito' : ''}" data-i="${i}">
      <button type="button" class="passo-marca" aria-pressed="${feitos.has(i)}" aria-label="Marcar passo ${i + 1} como feito"><span>${i + 1}</span><svg><use href="#i-check"/></svg></button>
      <div class="passo-texto"><strong>${esc(tituloTxt)}</strong>${continua ? ` <span class="passo-det">${mdInline(p.det.trim())}</span>` : `<div class="passo-det md">${md(p.det)}</div>`}</div>
      ${acao ? `<button type="button" class="botao secundario pequeno passo-acao" data-i="${i}">${acao.pergunta ? '<svg><use href="#i-chat"/></svg>' : ''}${esc(acao.rotulo)}</button>` : '<span></span>'}
    </li>`;
  }).join('');
  const atualizar = () => {
    const n = feitos.size;
    $('#progresso').innerHTML = `<span>${n} de ${passos.length}</span><span class="progresso-barra"><span style="width:${Math.round((n / passos.length) * 100)}%"></span></span>`;
  };
  atualizar();
  $$('.passo-marca', ol).forEach((b) => b.addEventListener('click', () => {
    const li = b.closest('.passo');
    const i = Number(li.dataset.i);
    if (feitos.has(i)) feitos.delete(i); else feitos.add(i);
    li.classList.toggle('feito', feitos.has(i));
    b.setAttribute('aria-pressed', String(feitos.has(i)));
    memoria.gravar(chave, [...feitos]);
    atualizar();
  }));
  $$('.passo-acao', ol).forEach((b) => b.addEventListener('click', () => {
    const acao = ACOES_ROTEIRO[Number(b.dataset.i)];
    if (acao.pergunta) perguntar(acao.pergunta);
    else if (acao.rolar) $(acao.rolar).scrollIntoView({ behavior: 'smooth', block: 'start' });
    else if (acao.agenda) baixarAgenda({ uasg: estado.om.unidade_codigo });
  }));
}

async function baixarAgenda(params) {
  try {
    const r = await dados('agenda', params);
    baixar(`/arquivos/${encodeURIComponent(r.arquivo)}`);
    avisar(`Agenda criada com ${r.eventos} vencimento${r.eventos === 1 ? '' : 's'}. Importe o .ics no Outlook ou no Google Agenda.`);
  } catch (e) { avisar(e.message); }
}

/* ------------------------------------------------------------------ ficha do contrato */
let fichaFoco = null;
async function abrirFicha(id) {
  if (!id) return;
  fichaFoco = document.activeElement;
  const g = $('#ficha');
  g.hidden = false;
  document.body.style.overflow = 'hidden';
  $('#ficha-titulo').textContent = 'Carregando…';
  $('#ficha-corpo').innerHTML = '<span class="esq" style="height:60px"></span><span class="esq"></span><span class="esq" style="width:60%"></span>';
  $('.gaveta-topo .botao-icone', g).focus();
  try {
    const f = await dados('ficha', { id });
    if (!f.identificacao) throw new Error('Contrato não encontrado.');
    renderFicha(f);
  } catch (e) {
    $('#ficha-titulo').textContent = 'Ficha indisponível';
    $('#ficha-corpo').innerHTML = `<p>${esc(e.message)}</p>`;
  }
}

function fecharFicha() {
  $('#ficha').hidden = true;
  document.body.style.overflow = '';
  if (fichaFoco && document.contains(fichaFoco)) fichaFoco.focus();
}

function renderFicha(f) {
  const idt = f.identificacao;
  const pz = f.prazos;
  const vl = f.valores;
  const fx = faixa(pz.faixa);
  $('#ficha-titulo').textContent = `Contrato ${idt.numero_contrato || ''} — ${titulo(idt.fornecedor || '')}`;
  const dado = (rot, val) => `<div><dt>${esc(rot)}</dt><dd>${esc(val ?? '—')}</dd></div>`;
  const forn = f.fornecedor_na_om;
  const outros = f.outros_contratos_do_fornecedor_na_om || [];
  const diasNum = pz.dias_para_vencer;
  $('#ficha-corpo').innerHTML = `
    <div class="ficha-prazo ${fx.classe}">
      <div class="dias">${diasNum === null || diasNum === undefined ? '—' : Math.abs(diasNum)}<small>${diasNum === null || diasNum === undefined ? 'sem data de fim' : diasNum >= 0 ? 'dias para vencer' : 'dias desde o fim'}</small></div>
      <p><span class="selo ${fx.classe}">${esc(fx.rotulo)}</span><br><strong>${esc(pz.acao_indicada || '')}</strong><br><small>Fim da vigência em ${esc(pz.fim_vigencia)} · referência ${esc(pz.data_referencia)}</small></p>
    </div>
    <section class="ficha-bloco"><h3>Objeto</h3><p class="ficha-objeto">${esc(idt.objeto)}</p></section>
    <section class="ficha-bloco"><h3>Dados do contrato</h3>
      <dl class="dados">
        ${dado('Fornecedor', idt.fornecedor)}
        ${dado('Valor global', vl.valor_global)}
        ${dado('Valor inicial', vl.valor_inicial)}
        ${dado('Parcela', vl.numero_parcelas ? `${vl.valor_parcela} × ${vl.numero_parcelas}` : vl.valor_parcela)}
        ${dado('Vigência', `${pz.inicio_vigencia} a ${pz.fim_vigencia}`)}
        ${dado('Assinatura', pz.assinatura)}
        ${dado('Publicado no PNCP', f.registro_publico?.publicado_no_pncp_em)}
        ${dado('Retificações no PNCP', f.registro_publico?.retificacoes ?? 0)}
        ${dado('Tipo', idt.tipo_instrumento)}
        ${dado('Categoria', idt.categoria)}
        ${dado('Nº de controle PNCP', idt.numero_controle_pncp)}
        ${dado('Contratação de origem', idt.contratacao_de_origem)}
      </dl>
    </section>
    <section class="ficha-bloco"><h3>Pontos de atenção</h3>
      ${(f.pontos_de_atencao || []).length
    ? `<div class="caixa-atencao"><ul class="lista-ficha">${f.pontos_de_atencao.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>`
    : '<div class="caixa-neutra">Nenhum ponto de atenção no registro público.</div>'}
    </section>
    ${forn ? `<section class="ficha-bloco"><h3>Este fornecedor na OM</h3>
      <p>Participa com <strong>${esc(String(forn.participacao_pct).replace('.', ','))}%</strong> do valor vigente da OM (posição ${esc(forn.posicao_na_unidade)} de ${esc(forn.fornecedores_unidade)} fornecedores, ${esc(forn.qtd_instrumentos)} instrumento${Number(forn.qtd_instrumentos) === 1 ? '' : 's'}).
      ${forn.alerta_concentracao ? ' <span class="selo ambar">Concentração a avaliar</span>' : ''}</p>
      ${outros.length ? `<ul class="lista-ficha" style="margin-top:8px">${outros.map((o) => `<li>${esc(o.numero_contrato || o.numero_controle_pncp)} · ${esc(o.objeto)} · fim ${esc(dataBR(o.vigencia_fim))} (${esc(o.situacao)})</li>`).join('')}</ul>` : ''}
    </section>` : ''}
    <section class="ficha-bloco"><h3>Não consta na base — peça no processo ou ao fiscal anterior</h3>
      <div class="caixa-neutra"><ul class="lista-ficha">${(f.nao_consta_no_kit || []).map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>
    </section>
    <div class="ficha-acoes">
      <button type="button" class="botao primario" data-f="explicar"><svg><use href="#i-chat"/></svg>Explicar com o Mentor</button>
      <button type="button" class="botao secundario" data-f="relatorio"><svg><use href="#i-doc"/></svg>Relatório em Word</button>
      <button type="button" class="botao secundario" data-f="agenda"><svg><use href="#i-calendario"/></svg>Agenda (.ics)</button>
      ${idt.link_pncp ? `<a class="botao secundario" href="${esc(idt.link_pncp)}" target="_blank" rel="noopener"><svg><use href="#i-externo"/></svg>Ver no PNCP</a>` : ''}
    </div>
    <p class="subtitulo">Fonte: kit PNCP (${esc(pz.data_referencia)}). ${esc(f.limitacao || '')}</p>`;
  const nome = `o contrato ${idt.numero_contrato} (nº de controle PNCP ${idt.numero_controle_pncp})`;
  $('[data-f="explicar"]').addEventListener('click', () => { fecharFicha(); perguntar(`Vou fiscalizar ${nome}. Me explique esse contrato em linguagem simples e diga o que eu preciso fazer agora.`); });
  $('[data-f="relatorio"]').addEventListener('click', () => { fecharFicha(); perguntar(`Gere um relatório em Word de ${nome} para eu levar ao gestor, com as orientações do que verificar.`); });
  $('[data-f="agenda"]').addEventListener('click', () => baixarAgenda({ id: idt.numero_controle_pncp }));
}

/* ------------------------------------------------------------------ chat com o agente */
const ROTULOS = {
  buscar_om: 'Localizando a OM',
  meus_contratos: 'Lendo a carteira de contratos',
  ficha_contrato: 'Abrindo a ficha do contrato',
  listar_alertas: 'Verificando os alertas',
  guia_do_fiscal: 'Consultando o guia do fiscal',
  gerar_relatorio: 'Gerando o documento',
  exportar_agenda: 'Criando a agenda',
  consultar_sql: 'Consultando o banco (SQL)',
};

const SUGESTOES = [
  'Por onde eu começo?',
  'Quais contratos vencem primeiro e o que faço com cada um?',
  'Quais alertas a minha OM tem hoje?',
  'Escreva um e-mail ao gestor avisando dos contratos que vencem em até 180 dias.',
  'A empresa atrasou uma entrega. Como eu registro e cobro?',
  'Exporte os vencimentos para a minha agenda.',
];

function contexto() {
  const om = estado.om;
  return `[Contexto da tela: sou fiscal de contrato recém-designado na OM ${om.nome} (código UASG ${om.unidade_codigo}). `
    + 'Use esse código nas ferramentas e não me pergunte a OM de novo.]\n\n';
}

function semContexto(texto) {
  return String(texto || '').replace(/^\[Contexto da tela:[^\]]*\]\s*/, '');
}

function rolarChat(forcar = false) {
  const m = $('#mensagens');
  const perto = m.scrollHeight - m.scrollTop - m.clientHeight < 160;
  if (forcar || perto) m.scrollTop = m.scrollHeight;
}

function renderSugestoes() {
  $('#sugestoes').innerHTML = SUGESTOES.map((s) => `<button type="button" class="chip">${esc(s)}</button>`).join('');
  $$('#sugestoes .chip').forEach((b) => b.addEventListener('click', () => perguntar(b.textContent)));
}

function boasVindas() {
  const om = estado.om;
  const el = document.createElement('div');
  el.className = 'msg msg-mentor';
  el.innerHTML = `<span class="avatar"><svg><use href="#i-ancora"/></svg></span>
    <div class="msg-corpo"><div class="texto-mentor boas-vindas md">${md(
    `Olá! Sou o **Mentor do Fiscal**. Já sei que você é da **${titulo(om.nome)}** (UASG ${om.unidade_codigo}).\n\n`
    + 'Posso explicar cada contrato em linguagem simples, dizer o que fazer com o que está vencendo, '
    + 'montar a passagem com o fiscal anterior e gerar o **relatório em Word**, o **e-mail ao gestor** e a **agenda**.\n\n'
    + 'Os números vêm sempre do banco; você vê cada consulta acontecendo aqui no chat.')}</div></div>`;
  $('#mensagens').appendChild(el);
}

function bolhaUsuario(texto) {
  const el = document.createElement('div');
  el.className = 'msg msg-usuario';
  el.innerHTML = `<div class="balao">${esc(texto)}</div>`;
  $('#mensagens').appendChild(el);
  rolarChat(true);
}

function extrairResposta(fr) {
  const r = fr?.response || {};
  let d = null;
  if (r.structuredContent && typeof r.structuredContent === 'object') d = r.structuredContent;
  else if (Array.isArray(r.content)) {
    try { d = JSON.parse(r.content.map((c) => c.text || '').join('')); } catch { d = null; }
  } else if (r.result !== undefined) {
    d = r.result;
    if (typeof d === 'string') { try { d = JSON.parse(d); } catch { d = null; } }
  } else d = r;
  if (d && typeof d === 'object' && d.result && Object.keys(d).length === 1) d = d.result;
  return d && typeof d === 'object' ? d : null;
}

class Turno {
  constructor() {
    this.el = document.createElement('div');
    this.el.className = 'msg msg-mentor';
    this.el.innerHTML = `<span class="avatar"><svg><use href="#i-ancora"/></svg></span>
      <div class="msg-corpo"><div class="passos"></div><div class="conteudo"></div><div class="anexos"></div><div class="status"></div></div>`;
    this.passosEl = $('.passos', this.el);
    this.conteudoEl = $('.conteudo', this.el);
    this.anexosEl = $('.anexos', this.el);
    this.statusEl = $('.status', this.el);
    this.passos = [];
    this.bloco = null;
    this.buffer = '';
    this.inicio = Date.now();
    this.relogio = null;
    this.desenhoPendente = false;
    $('#mensagens').appendChild(this.el);
  }

  esperar() {
    const tick = () => {
      const s = Math.round((Date.now() - this.inicio) / 1000);
      const fase = this.bloco ? 'Escrevendo' : this.passos.some((p) => !p.feito) ? 'Consultando a base' : 'Pensando';
      this.statusEl.innerHTML = `<div class="status-espera"><span class="pontinhos"><i></i><i></i><i></i></span>${fase}… ${s} s</div>`;
    };
    tick();
    this.relogio = setInterval(tick, 1000);
  }

  parar() {
    clearInterval(this.relogio);
    this.relogio = null;
    this.statusEl.innerHTML = '';
    for (const p of this.passos) if (!p.feito) { p.feito = true; p.el.classList.add('parado'); }
    if (this.bloco) {
      this.bloco.classList.remove('cursor');
      if (!this.buffer.trim()) this.bloco.remove();
      this.bloco = null;
    }
  }

  passo(fc) {
    // No streaming a mesma chamada chega duas vezes (evento parcial e final): uma etapa só por id.
    if (fc.id && this.passos.some((p) => p.id === fc.id)) return;
    const nome = fc.name || 'ferramenta';
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'passo-ferramenta';
    b.title = 'Ver os parâmetros enviados ao banco';
    b.innerHTML = `<span class="estado"></span>${esc(ROTULOS[nome] || nome)} <code>${esc(nome)}</code>`;
    const det = document.createElement('div');
    det.className = 'detalhe-ferramenta';
    det.hidden = true;
    const args = fc.args || {};
    det.textContent = nome === 'consultar_sql' && args.sql ? args.sql : JSON.stringify(args, null, 2);
    b.addEventListener('click', () => { det.hidden = !det.hidden; });
    this.passosEl.append(b, det);
    this.passos.push({ id: fc.id, nome, el: b, feito: false });
    if (this.bloco) {
      this.bloco.classList.remove('cursor');
      if (!this.buffer.trim()) this.bloco.remove();
    }
    this.bloco = null;
    this.buffer = '';
    rolarChat();
  }

  resultado(fr) {
    const p = this.passos.find((x) => !x.feito && fr.id && x.id === fr.id) || this.passos.find((x) => !x.feito && x.nome === fr.name);
    const d = extrairResposta(fr);
    const falhou = Boolean(fr.response?.isError) || Boolean(d?.erro);
    if (p) { p.feito = true; p.el.classList.add(falhou ? 'falhou' : 'ok'); }
    if (d && !falhou) this.anexo(fr.name, d);
  }

  anexo(nome, d) {
    if ((nome === 'gerar_relatorio' || nome === 'exportar_agenda') && d.arquivo) {
      const ehAgenda = nome === 'exportar_agenda';
      const el = document.createElement('div');
      el.className = 'anexo';
      el.innerHTML = `<svg><use href="#${ehAgenda ? 'i-calendario' : 'i-doc'}"/></svg>
        <div><strong>${ehAgenda ? `Agenda com ${esc(d.eventos)} vencimento${Number(d.eventos) === 1 ? '' : 's'}` : 'Relatório em Word pronto'}</strong><small>${esc(d.arquivo)}</small></div>
        <a class="botao primario pequeno" href="/arquivos/${encodeURIComponent(d.arquivo)}" download>Baixar</a>`;
      this.anexosEl.appendChild(el);
    } else if (nome === 'gerar_relatorio' && d.corpo) {
      const el = document.createElement('div');
      el.className = 'anexo anexo-email';
      el.innerHTML = `<svg><use href="#i-email"/></svg>
        <div><strong>E-mail pronto para copiar</strong><small>Assunto: ${esc(d.assunto || '')}</small></div>
        <button type="button" class="botao primario pequeno">Copiar</button>
        <pre></pre>`;
      $('pre', el).textContent = d.corpo;
      $('button', el).addEventListener('click', async () => {
        const texto = `Assunto: ${d.assunto || ''}\n\n${d.corpo}`;
        try { await navigator.clipboard.writeText(texto); avisar('E-mail copiado. Cole no seu correio.'); } catch {
          const r = document.createRange(); r.selectNodeContents($('pre', el));
          const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
          avisar('Texto selecionado: use Ctrl+C / ⌘C para copiar.');
        }
      });
      this.anexosEl.appendChild(el);
    }
    rolarChat();
  }

  desenhar() {
    if (this.desenhoPendente) return;
    this.desenhoPendente = true;
    requestAnimationFrame(() => {
      this.desenhoPendente = false;
      if (this.bloco) { this.bloco.innerHTML = md(this.buffer); rolarChat(); }
    });
  }

  novoBloco() {
    const b = document.createElement('div');
    b.className = 'texto-mentor md';
    this.conteudoEl.appendChild(b);
    return b;
  }

  textoParcial(t) {
    if (!t) return;
    if (!this.bloco) { this.bloco = this.novoBloco(); this.buffer = ''; }
    this.bloco.classList.add('cursor');
    this.buffer += t;
    this.desenhar();
  }

  textoFinal(t) {
    if (!t.trim()) return;
    const b = this.bloco || this.novoBloco();
    b.classList.remove('cursor');
    b.innerHTML = md(t);
    this.bloco = null;
    this.buffer = '';
    rolarChat();
  }

  erro(msg, podeRepetir) {
    this.parar();
    const bruto = String(msg || 'Erro desconhecido');
    let amigavel = 'O Mentor não conseguiu responder agora.';
    if (/503|UNAVAILABLE|high demand|overloaded/i.test(bruto)) amigavel = 'O modelo do Gemini está sobrecarregado neste momento (503). Costuma passar em alguns segundos.';
    else if (/429|RESOURCE_EXHAUSTED|quota|rate/i.test(bruto)) amigavel = 'O limite de uso do plano gratuito do Gemini foi atingido (429). Aguarde um pouco ou troque o MODELO_GEMINI no .env.';
    else if (/API key|PERMISSION_DENIED|401|403/i.test(bruto)) amigavel = 'A chave do Gemini foi recusada. Confira a GOOGLE_API_KEY no .env.';
    else if (/Failed to fetch|NetworkError|502|504/i.test(bruto)) amigavel = 'Não consegui falar com o agente. Confira se o contêiner hk-agente está rodando.';
    const el = document.createElement('div');
    el.className = 'erro-chat';
    el.innerHTML = `<strong>${esc(amigavel)}</strong><small>${esc(bruto.slice(0, 300))}</small>`;
    if (podeRepetir && estado.ultima) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'botao secundario pequeno';
      b.textContent = 'Tentar de novo';
      b.addEventListener('click', () => { el.remove(); perguntar(estado.ultima, { repetir: true }); });
      el.appendChild(b);
    }
    this.anexosEl.appendChild(el);
    rolarChat(true);
  }

  evento(ev) {
    if (ev.error) { this.erro(typeof ev.error === 'string' ? ev.error : JSON.stringify(ev.error), true); return 'erro'; }
    if (ev.errorCode || ev.errorMessage) { this.erro(`${ev.errorCode || ''} ${ev.errorMessage || ''}`.trim(), true); return 'erro'; }
    const partes = ev.content?.parts || [];
    const textos = [];
    for (const p of partes) {
      if (p.thought) continue;
      if (p.functionCall) { if (!ev.partial) this.passo(p.functionCall); }
      else if (p.functionResponse) this.resultado(p.functionResponse);
      else if (typeof p.text === 'string') textos.push(p.text);
    }
    if (textos.length && ev.author !== 'user') {
      if (ev.partial) this.textoParcial(textos.join('')); else this.textoFinal(textos.join(''));
    }
    return 'ok';
  }
}

async function criarSessao() {
  const r = await fetch(`${API_AGENTE}/apps/${APP}/users/${USUARIO}/sessions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  if (!r.ok) throw new Error(`Não consegui abrir a conversa (${r.status}): ${(await r.text()).slice(0, 200)}`);
  const s = await r.json();
  estado.sessao = { id: s.id, contexto: false };
  memoria.gravar(`mf:sessao:${estado.om.unidade_codigo}`, estado.sessao);
}

async function prepararChat() {
  $('#mensagens').innerHTML = '';
  boasVindas();
  renderSugestoes();
  estado.sessao = null;
  const salvo = memoria.ler(`mf:sessao:${estado.om.unidade_codigo}`);
  if (salvo?.id) {
    try {
      const r = await fetch(`${API_AGENTE}/apps/${APP}/users/${USUARIO}/sessions/${encodeURIComponent(salvo.id)}`);
      if (r.ok) {
        const s = await r.json();
        estado.sessao = { id: s.id, contexto: salvo.contexto || (s.events || []).length > 0 };
        redesenharHistorico(s.events || []);
        return;
      }
    } catch { /* cria outra */ }
  }
  try { await criarSessao(); } catch (e) {
    const t = new Turno(); t.erro(e.message, false);
  }
}

function redesenharHistorico(eventos) {
  let turno = null;
  for (const ev of eventos) {
    if (ev.author === 'user') {
      const texto = (ev.content?.parts || []).map((p) => p.text || '').join('');
      if (texto) { bolhaUsuario(semContexto(texto)); turno = new Turno(); }
      continue;
    }
    if (!turno) turno = new Turno();
    turno.evento(ev);
  }
  rolarChat(true);
}

async function novaConversa() {
  if (estado.ocupado) { avisar('Aguarde o Mentor terminar a resposta.'); return; }
  $('#mensagens').innerHTML = '';
  boasVindas();
  try { await criarSessao(); } catch (e) { const t = new Turno(); t.erro(e.message, false); }
}

function ocupar(sim) {
  estado.ocupado = sim;
  $('#enviar').disabled = sim;
  $$('#sugestoes .chip').forEach((b) => { b.disabled = sim; });
}

async function perguntar(texto, { repetir = false } = {}) {
  texto = String(texto || '').trim();
  if (!texto || !estado.om) return;
  if (estado.ocupado) { avisar('Aguarde o Mentor terminar a resposta.'); return; }
  document.body.classList.add('chat-aberto');
  ocupar(true);
  estado.ultima = texto;
  if (!repetir) bolhaUsuario(texto);
  const turno = new Turno();
  turno.esperar();
  rolarChat(true);
  try {
    if (!estado.sessao) await criarSessao();
    const enviado = (estado.sessao.contexto ? '' : contexto()) + texto;
    const r = await fetch(`${API_AGENTE}/run_sse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({
        appName: APP, userId: USUARIO, sessionId: estado.sessao.id,
        newMessage: { role: 'user', parts: [{ text: enviado }] }, streaming: true,
      }),
    });
    if (r.status === 404) {
      await criarSessao();
      throw new Error('A conversa anterior expirou (o agente foi reiniciado). Abri uma nova: tente de novo.');
    }
    if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
    estado.sessao.contexto = true;
    memoria.gravar(`mf:sessao:${estado.om.unidade_codigo}`, estado.sessao);

    const leitor = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let teveErro = false;
    for (;;) {
      const { value, done } = await leitor.read();
      if (done) break;
      buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      let corte;
      while ((corte = buf.indexOf('\n\n')) >= 0) {
        const bloco = buf.slice(0, corte);
        buf = buf.slice(corte + 2);
        const dadosEv = bloco.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n');
        if (!dadosEv) continue;
        let ev;
        try { ev = JSON.parse(dadosEv); } catch { continue; }
        if (turno.evento(ev) === 'erro') teveErro = true;
      }
    }
    turno.parar();
    if (!teveErro && !turno.conteudoEl.children.length && !turno.anexosEl.children.length) {
      turno.erro('O agente terminou sem texto de resposta.', true);
    }
  } catch (e) {
    turno.erro(e.message, true);
  } finally {
    turno.parar();
    ocupar(false);
    $('#entrada').focus({ preventScroll: true });
  }
}

/* ------------------------------------------------------------------ ligações */
function ajustarAltura() {
  const t = $('#entrada');
  t.style.height = 'auto';
  t.style.height = `${Math.min(t.scrollHeight, 160)}px`;
}

function iniciar() {
  $('#link-rastro').href = `${location.protocol}//${location.hostname}:8000/dev-ui/?app=${APP}`;

  $('#busca-om').addEventListener('input', (e) => {
    clearTimeout(buscaTempo);
    const q = e.target.value.trim();
    if (q.length < 2) { buscaSeq += 1; $('#resultados-om').innerHTML = ''; return; }
    buscaTempo = setTimeout(() => buscarOM(q), 250);
  });
  $('#busca-om').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); const b = $('.resultado-om'); if (b) b.click(); else if (e.target.value.trim().length >= 2) buscarOM(e.target.value.trim()); }
    if (e.key === 'ArrowDown') { const b = $('.resultado-om'); if (b) { e.preventDefault(); b.focus(); } }
  });
  $('#resultados-om').addEventListener('keydown', (e) => {
    const itens = $$('.resultado-om');
    const i = itens.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' && i < itens.length - 1) { e.preventDefault(); itens[i + 1].focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); (i > 0 ? itens[i - 1] : $('#busca-om')).focus(); }
  });
  $$('.atalhos .chip').forEach((b) => b.addEventListener('click', () => abrirPorUasg(b.dataset.uasg)));

  $('#marca').addEventListener('click', (e) => { e.preventDefault(); if (!estado.om) return; window.scrollTo({ top: 0, behavior: 'smooth' }); });
  $('#trocar-om').addEventListener('click', () => { memoria.apagar('mf:om'); $('#busca-om').value = ''; $('#resultados-om').innerHTML = ''; mostrarInicio(); });

  $$('.aba').forEach((a) => a.addEventListener('click', () => trocarSituacao(a.dataset.situacao)));
  let tempoBusca;
  $('#busca-contrato').addEventListener('input', (e) => {
    clearTimeout(tempoBusca);
    tempoBusca = setTimeout(() => { estado.busca = e.target.value.trim(); carregarTabela(); }, 300);
  });

  $('#baixar-agenda').addEventListener('click', () => baixarAgenda({ uasg: estado.om.unidade_codigo }));
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-pergunta]');
    if (b) perguntar(b.dataset.pergunta);
  });

  $('#ficha').addEventListener('click', (e) => { if (e.target.closest('[data-fechar]')) fecharFicha(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('#ficha').hidden) fecharFicha();
    else if (e.key === 'Escape' && document.body.classList.contains('chat-aberto')) document.body.classList.remove('chat-aberto');
  });

  $('#form-chat').addEventListener('submit', (e) => {
    e.preventDefault();
    const t = $('#entrada');
    const texto = t.value;
    if (!texto.trim() || estado.ocupado) return;
    t.value = '';
    ajustarAltura();
    perguntar(texto);
  });
  $('#entrada').addEventListener('input', ajustarAltura);
  $('#entrada').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#form-chat').requestSubmit(); }
  });
  $('#nova-conversa').addEventListener('click', novaConversa);
  $('#abrir-chat').addEventListener('click', () => { document.body.classList.add('chat-aberto'); $('#entrada').focus(); });
  $('#fechar-chat').addEventListener('click', () => document.body.classList.remove('chat-aberto'));

  const om = memoria.ler('mf:om');
  if (om?.unidade_codigo) abrirPainel(om); else mostrarInicio();
}

iniciar();
