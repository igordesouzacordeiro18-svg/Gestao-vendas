import json
import os
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from datetime import datetime, timedelta, timezone # Importa o timezone
from collections import defaultdict
from functools import wraps
from flask import Flask, render_template, request, redirect, jsonify, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

# Define a constante do fuso horário de Brasília no topo
FUSO_BRASILIA = timezone(timedelta(hours=-3))

app = Flask(__name__)

# === CONFIGURAÇÕES DE SEGURANÇA E BANCO DE DADOS ===
app.secret_key = os.getenv("SECRET_KEY", "uma_chave_criptografica_muito_segura_aqui")

db_uri = os.getenv("DATABASE_URL", "sqlite:///sistema.db")
if db_uri.startswith("postgres://"):
    db_uri = db_uri.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 🌟 ADICIONE ESTE BLOCO AQUI (Corrige a conexão caindo no Render/Postgres):
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    "pool_timeout": 30,
}

db = SQLAlchemy(app)

# =========================================================
# CONFIGURAÇÃO DE E-MAIL (BREVO API - NUVEM)
# =========================================================
configuration = sib_api_v3_sdk.Configuration()
configuration.api_key['api-key'] = os.getenv("BREVO_API_KEY")
serializer = URLSafeTimedSerializer(app.secret_key)
#--------------------------------------------------------

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha = db.Column(db.String(200), nullable=False) # Guardará a senha criptografada (hash)
    cargo = db.Column(db.String(20), default="funcionario") # "admin" ou "funcionario"
    status = db.Column(db.String(20), default='ativo') # 'ativo' ou 'bloqueado'
    validade_plano = db.Column(db.DateTime, nullable=False)

    primeiro_acesso = db.Column(db.Boolean, default=True)

    def plano_expirado(self):
        return datetime.now() > self.validade_plano
    

class Produto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    preco = db.Column(db.Float, nullable=False)
    estoque = db.Column(db.Integer, default=0)
    estoque_minimo = db.Column(db.Integer, default=0)
    custo = db.Column(db.Float, default=0.0)  # 🌟 ADICIONE ESTA LINHA!


class Caixa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    
    aberto = db.Column(db.Boolean, default=False)
    valor_inicial = db.Column(db.Float, default=0.0)
    vendas_periodo = db.Column(db.Float, default=0.0)
    saldo_final = db.Column(db.Float, default=0.0)
    data_abertura = db.Column(db.String(20))   # Ex: "07/07/2026 18:48"
    data_fechamento = db.Column(db.String(20)) # Fica vazio enquanto estiver aberto


class Venda(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    caixa_id = db.Column(db.Integer, db.ForeignKey('caixa.id'), nullable=True)
    
    valor_total = db.Column(db.Float, nullable=False)
    desconto = db.Column(db.Float, default=0.0) 
    data = db.Column(db.String(20))
    produtos_vendidos = db.Column(db.Text, nullable=False)
    
    pagamento = db.Column(db.Text, nullable=True) 
    
    status = db.Column(db.String(20), default='CONCLUIDA')
    motivo_cancelamento = db.Column(db.Text, nullable=True)

class Troca(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False) # Garanta que a FK aponte para sua tabela de usuários (ex: usuario.id ou user.id)
    data = db.Column(db.String(30), nullable=False) # Aumentado levemente para 30 por garantia com formatos de data
    produtos_devolvidos = db.Column(db.Text, nullable=False)  # Armazena a lista de devolvidos como string JSON
    produtos_recebidos = db.Column(db.Text, nullable=False)    # Armazena a lista de novos como string JSON
    credito = db.Column(db.Float, nullable=False)
    total_compra = db.Column(db.Float, nullable=False)
    saldo_diferenca = db.Column(db.Float, nullable=False)
    forma_pagamento_diferenca = db.Column(db.String(50), nullable=True) # Ex: "Dinheiro", "Cartão de Crédito", "N/A"
    parcelas = db.Column(db.Integer, default=1)                         # Quantidade de parcelas (padrão 1)


# === EMAIL DO ADMINISTRADOR PRINCIPAL ===
EMAIL_ADMIN = "igordesouzacordeiro18@gmail.com"


# === CRIAÇÃO AUTOMÁTICA DAS TABELAS E DO USUÁRIO ADMIN ===
with app.app_context():
    db.create_all()
    
    # Verifica se o administrador principal já existe; se não, cria automaticamente
    admin_existente = Usuario.query.filter_by(email=EMAIL_ADMIN).first()
    if not admin_existente:
        senha_padrao = generate_password_hash("#Cordeiro400")  # Crie a senha inicial
        validade_vitalicia = datetime.now() + timedelta(days=36500)
        
        novo_admin = Usuario(
            email=EMAIL_ADMIN,
            senha=senha_padrao,
            validade_plano=validade_vitalicia,
            primeiro_acesso=False
        )
        db.session.add(novo_admin)
        db.session.commit()
        print("✅ Usuário Administrador principal criado com sucesso!")


def obter_dados_do_cliente():
    """Função mágica para carregar e garantir a estrutura limpa de cada cliente"""
    id_logado = session.get("usuario_id")
    if not id_logado:
        return None
        
    dados_globais = carregar_dados()
    id_key = str(id_logado)
    
    # Se o cliente não tiver dados ainda, cria do zero
    if id_key not in dados_globais:
        dados_globais[id_key] = {
            "caixa": {"aberto": False, "data_abertura": "", "valor_inicial": 0.0, "vendas_periodo": 0.0},
            "vendas": [],
            "produtos": []
        }
        salvar_dados(dados_globais)
        
    return dados_globais[id_key]
# =======================================================
# ROTAS EXCLUSIVAS DO ADMINISTRADOR (PROTEGIDAS)
# =======================================================


def apenas_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        id_logado = session.get("usuario_id")
        if not id_logado:
            return redirect(url_for("login"))

        # Se na sessão estiver como 'funcionario', bloqueia (mesmo que seja o dono testando)
        cargo_sessao = session.get("cargo", "funcionario")
        
        if cargo_sessao != "admin":
            flash("⚠️ Acesso restrito a Gerentes/Administradores.", "danger")
            return redirect(url_for("dashboard")) # Redireciona para o dashboard, não pro login!
            
        return f(*args, **kwargs)
    return decorated_function

def verificar_se_eh_admin():
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return False
    user = Usuario.query.get(usuario_id)
    if not user or not user.email:
        return False
    return user.email.strip().lower() == EMAIL_ADMIN.lower()

@app.route("/admin")
def painel_admin():
    if not verificar_se_eh_admin():
        flash("❌ Acesso negado! Esta área é restrita apenas ao administrador principal.")
        return redirect(url_for("dashboard"))
    
    todos_clientes = Usuario.query.all()
    return render_template("admin.html", clientes=todos_clientes)

@app.route("/admin/cadastrar", methods=["POST"])
def admin_cadastrar_cliente():
    if not verificar_se_eh_admin():
        flash("❌ Acesso negado! Ação não permitida.")
        return redirect(url_for("dashboard"))
        
    email = request.form.get("email", "").strip()
    senha = request.form.get("senha")
    
    if Usuario.query.filter_by(email=email).first():
        flash("Esse e-mail já está cadastrado no sistema!")
        return redirect(url_for("painel_admin"))
        
    senha_cripto = generate_password_hash(senha)
    
    # Se cadastrar o e-mail do admin, dá validade de 100 anos (vitalício)
    if email.lower() == EMAIL_ADMIN.lower():
        validade = datetime.now() + timedelta(days=36500)
    else:
        validade = datetime.now() + timedelta(days=30)
    
    novo_cliente = Usuario(email=email, senha=senha_cripto, validade_plano=validade)
    db.session.add(novo_cliente)
    db.session.commit()
    
    flash(f"Cliente {email} criado com sucesso!")
    return redirect(url_for("painel_admin"))

@app.route("/admin/alterar_status/<int:id>", methods=["POST"])
def admin_alterar_status(id):
    if not verificar_se_eh_admin():
        flash("❌ Acesso negado! Ação não permitida.")
        return redirect(url_for("dashboard"))
        
    user = Usuario.query.get_or_404(id)
    user.status = "bloqueado" if user.status == "ativo" else "ativo"
    db.session.commit()
    
    flash(f"Status do usuário {user.email} atualizado!")
    return redirect(url_for("painel_admin"))

@app.route("/admin/renovar/<int:id>", methods=["POST"])
def admin_renovar_plano(id):
    if not verificar_se_eh_admin():
        flash("❌ Acesso negado! Ação não permitida.")
        return redirect(url_for("dashboard"))
        
    user = Usuario.query.get_or_404(id)
    if user.plano_expirado():
        user.validade_plano = datetime.now() + timedelta(days=30)
    else:
        user.validade_plano = user.validade_plano + timedelta(days=30)
        
    db.session.commit()
    flash(f"Plano de {user.email} renovado por mais 30 dias!")
    return redirect(url_for("painel_admin"))

@app.route("/alternar-modo", methods=["POST"])
def alternar_modo():
    dados = request.get_json() or {}
    senha = dados.get("senha")
    
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return jsonify({"sucesso": False, "mensagem": "Usuário não autenticado."})

    usuario = Usuario.query.get(usuario_id)
    
    # Valida a senha da conta logada para virar Admin
    if usuario and check_password_hash(usuario.senha, senha):
        session["cargo"] = "admin"  # Libera as abas no Jinja2
        return jsonify({"sucesso": True, "mensagem": "Modo Gerente ativado!"})
    else:
        return jsonify({"sucesso": False, "mensagem": "Senha incorreta!"})


@app.route("/bloquear-modo", methods=["POST"])
def bloquear_modo():
    # Permite mudar a sessão para funcionário (útil para testes do dono)
    session["cargo"] = "funcionario"
    return jsonify({"sucesso": True, "mensagem": "Modo Funcionário ativado!"})


# ROTA PARA DELETAR USUÁRIO (ÁREA ADMIN)
@app.route("/admin/deletar-usuario/<int:id>", methods=["POST"])
def admin_deletar_usuario(id):
    id_logado = session.get("usuario_id")
    usuario_logado = Usuario.query.get(id_logado) if id_logado else None
    
    if not usuario_logado or usuario_logado.email != "igordesouzacordeiro18@gmail.com":
        flash("🚫 Acesso não autorizado!", "danger")
        return redirect("/dashboard")

    if usuario_logado.id == id:
        flash("⚠️ Você não pode deletar a sua própria conta de Administrador!", "danger")
        return redirect("/admin")

    usuario_para_deletar = Usuario.query.get_or_404(id)

    try:
        # Apaga TODOS os registros vinculados ao usuário em cascata
        Produto.query.filter_by(usuario_id=id).delete()
        Venda.query.filter_by(usuario_id=id).delete()
        Caixa.query.filter_by(usuario_id=id).delete()
        
        # ⚠️ IMPORTANTE: Limpa trocas e despesas para evitar a quebra de Foreign Key
        if 'Troca' in globals():
            Troca.query.filter_by(usuario_id=id).delete()
        if 'Despesa' in globals():
            Despesa.query.filter_by(usuario_id=id).delete()
        
        # Por fim, apaga o usuário
        db.session.delete(usuario_para_deletar)
        db.session.commit()

        flash(f"🗑️ Usuário {usuario_para_deletar.email} e todos os seus registros foram excluídos!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao deletar usuário: {e}", "danger")

    return redirect("/admin")


# =======================================================
# ROTAS DE DEFINIÇÃO DE SENHA
# =======================================================

# 1. Mostra a página para o primeiro acesso
@app.route("/definir-senha")
def definir_senha():
    if not session.get("usuario_id"):
        return redirect("/")
    return render_template("definir_senha.html")

# 2. Valida e grava a nova senha oficial
@app.route("/salvar-nova-senha", methods=["POST"])
def salvar_nova_senha():
    if not session.get("usuario_id"):
        return redirect("/")
        
    nova_senha = request.form.get("nova_senha") 
    confirmar_senha = request.form.get("confirmar_senha")
    
    # Valida se os campos vieram preenchidos
    if nova_senha and confirmar_senha:
        # Trava do Python: se forem diferentes, recarrega a página avisando o usuário
        if nova_senha != confirmar_senha:
            return render_template("definir_senha.html", erro="As senhas digitadas não coincidem. Tente novamente!")
            
        from werkzeug.security import generate_password_hash
        
        usuario = Usuario.query.get(session["usuario_id"])
        
        if usuario:
            usuario.senha = generate_password_hash(nova_senha)
            usuario.primeiro_acesso = False
            
            db.session.commit()
            session.clear()
            
            print("🎉 SENHA ATUALIZADA COM SUCESSO NO BANCO DE DADOS!")
            return redirect("/")
            
    return render_template("definir_senha.html", erro="Por favor, preencha todos os campos.")


# =======================================================
# ROTAS DE RECUPERAÇÃO DE SENHA VIA E-MAIL
# =======================================================

# 1. Rota para solicitar o e-mail e enviar o link seguro
@app.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "POST":
        email_digitado = request.form.get("email", "").strip().lower()

        try:
            usuario = Usuario.query.filter_by(email=email_digitado).first()

            if usuario:
                token = serializer.dumps(email_digitado, salt="recuperar-senha-salt")
                link_redefinicao = url_for("redefinir_senha_token", token=token, _external=True)

                # Instancia o cliente da API do Brevo
                api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
                
                send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
                    to=[{"email": email_digitado}],
                    sender={"name": "Suporte Sistema", "email": "igordesouzacordeiro18@gmail.com"},
                    subject="🔒 Recuperação de Senha",
                    html_content=f"""
                        <p>Olá!</p>
                        <p>Recebemos uma solicitação para redefinir sua senha.</p>
                        <p><a href="{link_redefinicao}">Clique aqui para redefinir sua senha</a> (Válido por 15 min)</p>
                    """
                )

                api_response = api_instance.send_transac_email(send_smtp_email)
                print(f"✅ E-mail enviado via Brevo: {api_response}")

        except ApiException as e:
            db.session.rollback()
            print(f"❌ ERRO BREVO API: {e}")
        except Exception as e:
            db.session.rollback()
            print(f"❌ ERRO GERAL: {e}")

        flash("Se o e-mail estiver cadastrado em nosso sistema, você receberá as instruções em instantes.")
        return redirect(url_for("login"))

    return render_template("esqueci_senha.html")


# 2. Rota que abre quando o cliente clica no link do e-mail
@app.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha_token(token):
    try:
        # Valida o token e verifica se expirou (15 minutos = 900 segundos)
        email = serializer.loads(token, salt="recuperar-senha-salt", max_age=900)
    except (SignatureExpired, BadTimeSignature):
        flash("❌ O link de redefinição é inválido ou expirou. Solicite um novo link.")
        return redirect(url_for("esqueci_senha"))

    if request.method == "POST":
        nova_senha = request.form.get("nova_senha", "").strip()
        confirmar_senha = request.form.get("confirmar_senha", "").strip()

        if not nova_senha or not confirmar_senha:
            return render_template("redefinir_senha_token.html", token=token, erro="Preencha todos os campos.")

        if nova_senha != confirmar_senha:
            return render_template("redefinir_senha_token.html", token=token, erro="As senhas não coincidem.")

        try:
            usuario = Usuario.query.filter_by(email=email).first()
            if usuario:
                usuario.senha = generate_password_hash(nova_senha)
                usuario.primeiro_acesso = False
                db.session.commit()

                flash("✅ Sua senha foi alterada com sucesso! Faça login com a nova senha.")
                return redirect(url_for("login"))
            else:
                flash("❌ Usuário não encontrado.")
                return redirect(url_for("esqueci_senha"))
        except Exception as e:
            db.session.rollback()
            print(f"❌ Erro ao atualizar senha no banco: {e}")
            return render_template("redefinir_senha_token.html", token=token, erro="Erro ao salvar a nova senha no banco de dados.")

    return render_template("redefinir_senha_token.html", token=token)


# =======================================================
# ROTA DE LOGIN PRINCIPAL
# =======================================================

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email_digitado = request.form.get("email", "").strip()
        senha_digitada = request.form.get("senha")

        usuario = Usuario.query.filter_by(email=email_digitado).first()

        if usuario:
            if check_password_hash(usuario.senha, senha_digitada):
                
                # 🌟 ADMIN NUNCA EXPIRA
                eh_admin = usuario.email and usuario.email.strip().lower() == EMAIL_ADMIN.lower()

                if not eh_admin and usuario.plano_expirado():
                    return render_template("login.html", erro="⚠️ Sua assinatura expirou. Regularize seu plano para acessar.")
                
                if usuario.status == "bloqueado":
                    return render_template("login.html", erro="❌ Acesso suspenso. Entre em contato com o administrador.")

                # 🔑 REGISTRO DA SESSÃO COM AS PERMISSÕES CORRETAS:
                session["usuario_id"] = usuario.id
                
                # Se for o e-mail do admin principal ou se a coluna cargo for admin, grava 'admin'
                cargo_efetivo = "admin" if (eh_admin or getattr(usuario, 'cargo', '') in ['admin', 'gerente']) else getattr(usuario, 'cargo', 'funcionario')
                session["cargo"] = cargo_efetivo
                
                if hasattr(usuario, 'primeiro_acesso') and usuario.primeiro_acesso:
                    return redirect(url_for("definir_senha"))
                
                return redirect(url_for("dashboard")) 
            
            else:
                return render_template("login.html", erro="E-mail ou senha incorretos.")
        else:
            return render_template("login.html", erro="E-mail ou senha incorretos.")

    return render_template("login.html")
#--------------------------------------------------------------------------------------------------
@app.route("/logout")
def logout():
    session.clear() # Limpa toda a sessão
    return redirect("/") # Manda de volta para o login

@app.route("/dashboard")
def dashboard():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # 1. Total de produtos cadastrados pelo usuário no banco
    total_produtos = Produto.query.filter_by(usuario_id=id_logado).count()

    # 2. Busca apenas o caixa que está EFETIVAMENTE ABERTO (aberto=True e sem data de fechamento)
    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True, data_fechamento=None).order_by(Caixa.id.desc()).first()
    
    total_vendas_periodo = 0
    valor_total_caixa = 0.0
    ultimas_vendas_lista = []

    # Variáveis para o resumo da notinha (do caixa atual)
    total_dinheiro = 0.0
    total_debito = 0.0
    total_credito = 0.0
    total_pix = 0.0

    if caixa_atual:
        # Busca todas as vendas que pertencem ao caixa que está aberto atualmente
        vendas_caixa = Venda.query.filter_by(usuario_id=id_logado, caixa_id=caixa_atual.id, status="CONCLUIDA").all()
        total_vendas_periodo = len(vendas_caixa)
        
        # 🟢 CÁLCULO DAS DESPESAS DO CAIXA
        total_despesas_caixa = 0.0
        try:
            despesas_db = Despesa.query.filter_by(usuario_id=id_logado, caixa_id=caixa_atual.id).all()
            total_despesas_caixa = sum(float(d.valor or 0) for d in despesas_db)
        except Exception:
            total_despesas_caixa = 0.0

        # Saldo Líquido do Caixa = Inicial + Vendas - Despesas
        valor_total_caixa = (caixa_atual.valor_inicial or 0.0) + (caixa_atual.vendas_periodo or 0.0) - total_despesas_caixa

        # Soma os totais por forma de pagamento no caixa atual
        for v in vendas_caixa:
            metodo = str(getattr(v, 'pagamento', getattr(v, 'forma_pagamento', 'dinheiro'))).lower()
            valor = float(v.valor_total or 0)

            if 'pix' in metodo:
                total_pix += valor
            elif 'débito' in metodo or 'debito' in metodo:
                total_debito += valor
            elif 'crédito' in metodo or 'credito' in metodo:
                total_credito += valor
            else:
                total_dinheiro += valor

        # Pega as últimas 5 vendas do período atual
        for v in reversed(vendas_caixa):
            if len(ultimas_vendas_lista) < 5:
                try:
                    itens = json.loads(v.produtos_vendidos)
                except Exception:
                    itens = []
                
                # Soma a quantidade total de itens vendidos nesta venda específica
                qtd_total_venda = sum(int(item.get("quantidade", 1)) for item in itens) if itens else 1

                ultimas_vendas_lista.append({
                    "id": v.id,
                    "total": v.valor_total,
                    "data": v.data,
                    "quantidade": qtd_total_venda, # 🌟 CHAVE QUE FALTAVA
                    "pagamento": getattr(v, 'pagamento', 'Dinheiro'),
                    "itens": itens
                })

    # 3. Estatísticas Gerais (para produtos mais vendidos)
    todas_vendas = Venda.query.filter_by(usuario_id=id_logado, status="CONCLUIDA").all()
    contador_produtos = {}

    for venda in todas_vendas:
        try:
            itens = json.loads(venda.produtos_vendidos)
        except Exception:
            itens = []

        for item in itens:
            nome_p = item.get("produto")
            qtd = int(item.get("quantidade", 0))
            if nome_p:
                contador_produtos[nome_p] = contador_produtos.get(nome_p, 0) + qtd

    produto_mais_vendido = "Nenhum"
    if contador_produtos:
        produto_mais_vendido = max(contador_produtos, key=contador_produtos.get)

    # Data e hora formatadas para o topo da notinha
    data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M")

    return render_template(
        "dashboard.html",
        total_vendas=total_vendas_periodo,
        total_produtos=total_produtos,
        valor_total=f"{valor_total_caixa:.2f}",
        ultimas_vendas=ultimas_vendas_lista,
        produto_mais_vendido=produto_mais_vendido,
        data_atual=data_hoje,
        total_dinheiro=f"{total_dinheiro:.2f}",
        total_debito=f"{total_debito:.2f}",
        total_credito=f"{total_credito:.2f}",
        total_pix=f"{total_pix:.2f}"
    )

# 1. ROTA PARA LISTAR OS PRODUTOS DO USUÁRIO LOGADO
@app.route("/produtos")
def produtos():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Busca no banco APENAS os produtos que pertencem ao usuário logado e ordena por nome
    produtos_usuario = Produto.query.filter_by(usuario_id=id_logado).order_by(Produto.nome.asc()).all()

    return render_template("produtos.html", produtos=produtos_usuario)


# 2. ROTA QUE APENAS MOSTRA A TELA DO FORMULÁRIO
@app.route("/novo-produto")
def novo_produto():
    if not session.get("usuario_id"):
        return redirect("/")
    return render_template("novo_produto.html")


# 3. ROTA QUE RECEBE OS DADOS DO HTML E SALVA DE VERDADE NO SQLITE
@app.route("/salvar-produto", methods=["POST"])
@apenas_admin
def cadastrar_produto():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Captura os dados enviados pelo formulário HTML
    nome = request.form.get("nome")
    preco_texto = request.form.get("preco")
    custo_texto = request.form.get("custo") or "0"
    estoque_texto = request.form.get("estoque") or "0"

    try:
        preco = float(preco_texto)
        custo = float(custo_texto)
        estoque = int(estoque_texto)
    except (ValueError, TypeError):
        return "❌ Valores numéricos inválidos enviados no formulário.", 400

    # Cria o novo objeto Produto usando o modelo do SQLite
    novo_produto = Produto(
        usuario_id=id_logado, # Vincula diretamente ao usuário dono da sessão!
        nome=nome.strip(),
        preco=preco,
        custo=custo,
        estoque=estoque
    )

    # Adiciona e salva permanentemente no banco de dados
    db.session.add(novo_produto)
    db.session.commit()

    print(f"📦 PRODUTO CADASTRADO NO SQLITE COM SUCESSO: {nome}")
    return redirect("/produtos")



@app.route("/salvar-venda", methods=["POST"])
def salvar_venda():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return jsonify({"erro": "❌ Usuário não autenticado"}), 401

    # 1. Busca o caixa aberto do usuário
    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True, data_fechamento=None).order_by(Caixa.id.desc()).first()
    
    if not caixa_atual:
        flash("❌ Abra o caixa antes de realizar uma venda!", "danger")
        return """
        <script>
            alert('❌ Abra o caixa antes de realizar uma venda.');
            window.location.href = '/caixa';
        </script>
        """

    # 2. Carrega o carrinho enviado pelo front-end
    carrinho_json = request.form.get("carrinho")
    if not carrinho_json:
        return jsonify({"erro": "❌ Carrinho vazio"}), 400

    try:
        carrinho = json.loads(carrinho_json)
    except json.JSONDecodeError:
        return jsonify({"erro": "❌ Erro ao processar os itens do carrinho."}), 400

    # Função auxiliar para converter valores numéricos com segurança
    def converter_para_float(val):
        if not val or str(val).strip() == "":
            return 0.0
        try:
            return float(str(val).replace(",", "."))
        except (ValueError, TypeError):
            return 0.0

    # 🏷️ CAPTURA O DESCONTO ENVIADO PELO FORMULÁRIO
    desconto = converter_para_float(request.form.get("desconto"))

    pagamento1 = request.form.get("pagamento1")
    valor1 = converter_para_float(request.form.get("valor1"))
    
    pagamento2 = request.form.get("pagamento2")
    valor2 = converter_para_float(request.form.get("valor2"))

    # Organiza os pagamentos
    pagamentos = []
    if pagamento1 and pagamento1.strip() != "":
        pagamentos.append({"tipo": pagamento1, "valor": valor1})
    if pagamento2 and pagamento2.strip() != "":
        pagamentos.append({"tipo": pagamento2, "valor": valor2})

    if not pagamentos:
        pagamentos.append({"tipo": "Dinheiro", "valor": 0.0})

    subtotal_produtos = 0.0
    itens_vendidos_lista = []

    # 3. Processa cada item do carrinho e calcula o subtotal bruto
    for item in carrinho:
        nome_bruto = item.get("nome", "")
        
        if " - R$" in nome_bruto:
            nome_produto = nome_bruto.split(" - R$")[0].strip()
        elif " - " in nome_bruto:
            nome_produto = nome_bruto.split(" - ")[0].strip()
        else:
            nome_produto = nome_bruto.strip()

        produto = Produto.query.filter_by(usuario_id=id_logado, nome=nome_produto).first()

        if not produto:
            produto = Produto.query.filter_by(usuario_id=id_logado, nome=nome_bruto.strip()).first()

        if not produto:
            return jsonify({"erro": f"❌ Produto '{nome_produto}' não foi encontrado!"}), 404

        try:
            qtd_vendida = int(item.get("quantidade", 1))
        except (ValueError, TypeError):
            qtd_vendida = 1

        produto.estoque = max(0, produto.estoque - qtd_vendida)

        subtotal = produto.preco * qtd_vendida
        subtotal_produtos += subtotal

        itens_vendidos_lista.append({
            "produto": produto.nome,
            "quantidade": qtd_vendida,
            "preco_unitario": produto.preco,
            "subtotal": subtotal
        })

    # 🏷️ APLICA O DESCONTO NO TOTAL FINAL (Garantindo que não fique negativo)
    total_geral = max(0.0, subtotal_produtos - desconto)

    # Se for pagamento simples e o valor veio 0.0, assume o total_geral (LÍQUIDO) da venda
    if len(pagamentos) == 1 and pagamentos[0]["valor"] == 0.0:
        pagamentos[0]["valor"] = total_geral

    pagamento_str = json.dumps(pagamentos, ensure_ascii=False)

    # Pega o horário correto do Brasil (UTC-3)
    data_br = datetime.now(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")

    # 4. Prepara os dados e registra a nova Venda
    dados_venda = {
        "usuario_id": id_logado,
        "caixa_id": caixa_atual.id,
        "valor_total": total_geral, # 💡 Salva o valor com desconto ($40,00)
        "data": data_br,
        "produtos_vendidos": json.dumps(itens_vendidos_lista, ensure_ascii=False),
        "pagamento": pagamento_str,
        "status": "CONCLUIDA"
    }

    # Se o seu model Venda no banco já tiver a coluna 'desconto', salva ela também
    if hasattr(Venda, 'desconto'):
        dados_venda["desconto"] = desconto

    nova_venda_db = Venda(**dados_venda)

    # 5. Atualiza o faturamento do Caixa com o valor LÍQUIDO real recebido
    caixa_atual.vendas_periodo += total_geral

    db.session.add(nova_venda_db)
    db.session.commit()

    print(f"💰 VENDA REGISTRADA! Total Líquido: R$ {total_geral:.2f} (Desconto: R$ {desconto:.2f})")
    return redirect("/historico")


@app.route("/excluir-venda/<int:id>")
def excluir_venda(id):
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    venda = Venda.query.filter_by(id=id, usuario_id=id_logado).first()

    if venda:
        # 1. Devolve os itens vendidos de volta ao estoque no SQLite
        try:
            itens = json.loads(venda.produtos_vendidos)
        except:
            itens = []

        for item in itens:
            produto = Produto.query.filter_by(usuario_id=id_logado, nome=item["produto"]).first()
            if produto:
                produto.estoque += item["quantidade"]

        # 2. Desconta o valor da venda do caixa ativo (se a venda pertencer ao caixa aberto)
        caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
        if caixa_atual and venda.caixa_id == caixa_atual.id:
            caixa_atual.vendas_periodo = max(0.0, caixa_atual.vendas_periodo - venda.valor_total)

        # 3. Deleta a venda
        db.session.delete(venda)
        db.session.commit()

    return redirect("/historico")


# 1. EXCLUIR PRODUTO DO BANCO
@app.route("/excluir-produto/<int:id>")
def excluir_produto(id):
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Garante que o produto pertence ao usuário logado antes de excluir (segurança!)
    produto = Produto.query.filter_by(id=id, usuario_id=id_logado).first()

    if produto:
        db.session.delete(produto)
        db.session.commit()

    return redirect("/produtos")


# 2. MOSTRAR A TELA DE EDIÇÃO COM OS DADOS DO BANCO
@app.route("/editar-produto/<int:id>")
def editar_produto(id):
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Busca o produto no SQLite
    produto = Produto.query.filter_by(id=id, usuario_id=id_logado).first()
    
    if not produto:
        return "Produto não encontrado", 404

    return render_template("editar_produto.html", produto=produto)


# 3. ATUALIZAR OS DADOS DO PRODUTO NO BANCO
@app.route("/atualizar-produto/<int:id>", methods=["POST"])
def atualizar_produto(id):
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    produto = Produto.query.filter_by(id=id, usuario_id=id_logado).first()

    if produto:
        produto.nome = request.form.get("nome")
        try:
            produto.preco = float(request.form.get("preco"))
            # Se o seu formulário de edição tiver estoque e custo, pode atualizar aqui também!
        except (ValueError, TypeError):
            return "❌ Preço inválido.", 400

        db.session.commit()

    return redirect("/produtos")

@app.route("/nova-venda")
def nova_venda():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    produtos_ordenados = Produto.query.filter_by(usuario_id=id_logado).order_by(Produto.nome.asc()).all()

    # Pega estritamente o ÚLTIMO registro de caixa do usuário
    ultimo_caixa = Caixa.query.filter_by(usuario_id=id_logado).order_by(Caixa.id.desc()).first()
    
    # O caixa só é considerado ABERTO se existir, estiver com aberto=True E não tiver data_fechamento
    caixa_aberto = False
    if ultimo_caixa and ultimo_caixa.aberto and not ultimo_caixa.data_fechamento:
        caixa_aberto = True

    return render_template(
        "nova_venda.html",
        produtos=produtos_ordenados,
        caixa_aberto=caixa_aberto
    )

@app.route("/historico")
def historico():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    vendas_db = Venda.query.filter_by(usuario_id=id_logado).all()
    vendas_formatadas = []
    
    for v in reversed(vendas_db):
        try:
            itens = json.loads(v.produtos_vendidos)
        except:
            itens = []

        pgto_raw = v.pagamento or "Não informado"
        pgto_exibicao = pgto_raw

        try:
            pgto_json = json.loads(pgto_raw)
            if isinstance(pgto_json, list):
                partes = [f"{p.get('tipo', 'Forma')}: R$ {float(p.get('valor', 0)):.2f}" for p in pgto_json]
                pgto_exibicao = " | ".join(partes)
        except (json.JSONDecodeError, TypeError):
            pgto_exibicao = pgto_raw
            
        vendas_formatadas.append({
            "id": v.id,
            "total": v.valor_total,
            "desconto": getattr(v, 'desconto', 0.0) or 0.0,
            "data": v.data,
            "itens": itens,
            "pagamento": pgto_exibicao,
            "status": getattr(v, 'status', 'CONCLUIDA') or 'CONCLUIDA',
            "motivo_cancelamento": getattr(v, 'motivo_cancelamento', '') or ''
        })

    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
    faturamento_caixa = caixa_atual.vendas_periodo if caixa_atual else 0.0

    trocas_db = Troca.query.filter_by(usuario_id=id_logado).all()
    trocas_formatadas = []

    for t in reversed(trocas_db):
        try:
            devolvidos = json.loads(t.produtos_devolvidos)
        except:
            devolvidos = []

        try:
            recebidos = json.loads(t.produtos_recebidos)
        except:
            recebidos = []

        trocas_formatadas.append({
            "id": t.id,
            "data": t.data,
            "credito": t.credito,
            "total_compra": t.total_compra,
            "saldo_diferenca": t.saldo_diferenca,
            "forma_pagamento_diferenca": getattr(t, 'forma_pagamento_diferenca', 'Troca Sem Diferença'),
            "parcelas": getattr(t, 'parcelas', 1),
            "devolvidos": devolvidos,
            "novos": recebidos
        })

    return render_template(
        "historico.html",
        vendas=vendas_formatadas, 
        trocas=trocas_formatadas, 
        total=faturamento_caixa
    )


@app.route('/cancelar-venda/<int:venda_id>', methods=['POST'])
def cancelar_venda(venda_id):
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    venda = Venda.query.filter_by(id=venda_id, usuario_id=id_logado).first()
    if not venda:
        return "Venda não encontrada", 404

    if getattr(venda, 'status', '') == 'CANCELADA':
        return "Venda já cancelada", 400

    motivo = request.form.get('motivo') or "Motivo não informado"

    # 1. Devolve os itens para o Estoque
    try:
        if venda.produtos_vendidos:
            itens = json.loads(venda.produtos_vendidos) if isinstance(venda.produtos_vendidos, str) else venda.produtos_vendidos
            
            for item in itens:
                nome_prod = item.get('produto') or item.get('nome')
                qtd = int(item.get('quantidade', 1))

                produto = Produto.query.filter_by(nome=nome_prod, usuario_id=id_logado).first()
                if produto:
                    produto.estoque += qtd
                    print(f"✅ Estoque devolvido: {produto.nome} +{qtd}")
    except Exception as e:
        print(f"❌ Erro ao estornar estoque: {e}")

    # 2. Pega o valor real da venda (trata valor_total ou total)
    valor_venda = getattr(venda, 'valor_total', None) or getattr(venda, 'total', 0.0) or 0.0

    # 3. Subtrai o valor APENAS do caixa aberto
    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
    if caixa_atual and valor_venda > 0:
        # Só desconta do caixa se o valor atual for suficiente
        caixa_atual.vendas_periodo = max(0.0, float(caixa_atual.vendas_periodo or 0.0) - float(valor_venda))
        print(f"💰 R$ {valor_venda} subtraído do caixa atual! Novo total: {caixa_atual.vendas_periodo}")

    # 4. Atualiza o Status da Venda
    venda.status = 'CANCELADA'
    venda.motivo_cancelamento = motivo

    db.session.commit()
    return "OK", 200


def processar_totais_pagamento(vendas_lista):
    """Lê as vendas (seja misto ou único) e devolve a soma exata por tipo de pagamento"""
    pix = 0.0
    dinheiro = 0.0
    cartao = 0.0

    for venda in vendas_lista:
        pgto_raw = getattr(venda, "forma_pagamento", None) or getattr(venda, "pagamento", None) or getattr(venda, "metodo_pagamento", None) or ""
        
        eh_misto = False

        # 1. Tenta interpretar se o campo de pagamento é um JSON
        if pgto_raw:
            try:
                pagamentos_detalhados = json.loads(pgto_raw) if isinstance(pgto_raw, str) else pgto_raw
                
                if isinstance(pagamentos_detalhados, list):
                    eh_misto = True
                    for p in pagamentos_detalhados:
                        tipo = str(p.get("tipo", "")).strip().lower()
                        
                        # Conversão segura para float
                        val_raw = p.get("valor", 0.0)
                        try:
                            val = float(str(val_raw).replace(",", ".")) if val_raw is not None else 0.0
                        except (ValueError, TypeError):
                            val = 0.0

                        if "pix" in tipo:
                            pix += val
                        elif any(termo in tipo for termo in ["cart", "debito", "débito", "credito", "crédito"]):
                            cartao += val
                        else:
                            dinheiro += val
            except Exception:
                eh_misto = False

        # 2. Se for pagamento simples (texto puro ou se não for lista no JSON)
        if not eh_misto:
            pgto = str(pgto_raw).strip().lower()
            
            # Pega o valor total da venda com conversão segura
            val_total_raw = getattr(venda, "valor_total", 0.0) or getattr(venda, "total", 0.0) or 0.0
            try:
                val = float(str(val_total_raw).replace(",", "."))
            except (ValueError, TypeError):
                val = 0.0

            if "pix" in pgto:
                pix += val
            elif any(termo in pgto for termo in ["cart", "debito", "débito", "credito", "crédito"]):
                cartao += val
            else:
                dinheiro += val

    return pix, dinheiro, cartao


@app.route("/relatorios")
def relatorios():
    return render_template("central_relatorios.html")


@app.route("/relatorio-financeiro")
@apenas_admin
def relatorio_financeiro():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    filtro = request.args.get("filtro", "hoje")
    hoje = datetime.now(FUSO_BRASILIA) if 'FUSO_BRASILIA' in globals() else datetime.now()

    todas_vendas = Venda.query.filter_by(usuario_id=id_logado).all()
    vendas_filtradas = []

    for venda in todas_vendas:
        if getattr(venda, 'status', '') == 'CANCELADA':
            continue

        str_data = str(getattr(venda, 'data', '')).strip()
        if not str_data:
            continue

        data_venda = None
        for fmt in ["%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"]:
            try:
                data_venda = datetime.strptime(str_data, fmt)
                break
            except (ValueError, TypeError):
                pass

        if not data_venda:
            continue

        if filtro == "hoje" and data_venda.strftime("%d/%m/%Y") == hoje.strftime("%d/%m/%Y"):
            vendas_filtradas.append(venda)
        elif filtro == "semana" and data_venda.isocalendar()[1] == hoje.isocalendar()[1] and data_venda.year == hoje.year:
            vendas_filtradas.append(venda)
        elif filtro == "mes" and data_venda.month == hoje.month and data_venda.year == hoje.year:
            vendas_filtradas.append(venda)
        elif filtro == "ano" and data_venda.year == hoje.year:
            vendas_filtradas.append(venda)

    total_vendas = sum(float(getattr(v, 'valor_total', 0) or getattr(v, 'total', 0) or 0) for v in vendas_filtradas)
    pix, dinheiro, cartao = processar_totais_pagamento(vendas_filtradas)

    # 🔁 PROCESSA TROCAS
    todas_trocas = Troca.query.filter_by(usuario_id=id_logado).all()
    total_diferenca_trocas = 0.0
    lucro_trocas = 0.0

    for troca in todas_trocas:
        if getattr(troca, 'status', '') == 'CANCELADA':
            continue

        str_data = str(getattr(troca, 'data', '')).strip()
        if not str_data:
            continue

        data_troca = None
        for fmt in ["%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"]:
            try:
                data_troca = datetime.strptime(str_data, fmt)
                break
            except (ValueError, TypeError):
                pass

        if not data_troca:
            continue

        incluir = False
        if filtro == "hoje" and data_troca.strftime("%d/%m/%Y") == hoje.strftime("%d/%m/%Y"):
            incluir = True
        elif filtro == "semana" and data_troca.isocalendar()[1] == hoje.isocalendar()[1] and data_troca.year == hoje.year:
            incluir = True
        elif filtro == "mes" and data_troca.month == hoje.month and data_troca.year == hoje.year:
            incluir = True
        elif filtro == "ano" and data_troca.year == hoje.year:
            incluir = True

        if incluir:
            saldo = float(getattr(troca, 'saldo_diferenca', 0) or 0)

            if saldo < 0:
                valor_recebido = abs(saldo)
                total_diferenca_trocas += valor_recebido
                forma = str(getattr(troca, 'forma_pagamento_diferenca', '') or getattr(troca, 'forma_pagamento', '')).strip()

                if "MISTO" in forma.upper():
                    partes = forma.split("(")[-1].replace(")", "").split("|")
                    for parte in partes:
                        if ":" in parte:
                            nome_f, val_f = parte.split(":", 1)
                            nome_upper = nome_f.upper()
                            try:
                                val_num = float(val_f.replace("R$", "").replace(",", ".").strip())
                            except ValueError:
                                val_num = 0.0

                            if "DINHEIRO" in nome_upper:
                                dinheiro += val_num
                            elif "PIX" in nome_upper:
                                pix += val_num
                            elif "CART" in nome_upper:
                                cartao += val_num
                else:
                    f_upper = forma.upper()
                    if "DINHEIRO" in f_upper:
                        dinheiro += valor_recebido
                    elif "PIX" in f_upper:
                        pix += valor_recebido
                    elif "CART" in f_upper:
                        cartao += valor_recebido

            try:
                prod_recebidos = json.loads(troca.produtos_recebidos) if isinstance(troca.produtos_recebidos, str) else (troca.produtos_recebidos or [])
                prod_devolvidos = json.loads(troca.produtos_devolvidos) if isinstance(troca.produtos_devolvidos, str) else (troca.produtos_devolvidos or [])

                custo_novos = sum(float(Produto.query.filter_by(usuario_id=id_logado, nome=item.get("nome") or item.get("produto")).first().custo or 0) * int(item.get("quantidade", 0)) for item in prod_recebidos if Produto.query.filter_by(usuario_id=id_logado, nome=item.get("nome") or item.get("produto")).first())
                custo_devolvidos = sum(float(Produto.query.filter_by(usuario_id=id_logado, nome=item.get("nome") or item.get("produto")).first().custo or 0) * int(item.get("quantidade", 0)) for item in prod_devolvidos if Produto.query.filter_by(usuario_id=id_logado, nome=item.get("nome") or item.get("produto")).first())

                lucro_trocas += (abs(saldo) if saldo < 0 else 0) + custo_devolvidos - custo_novos
            except Exception:
                pass

    total_geral = total_vendas + total_diferenca_trocas

    lucro_vendas = 0.0
    for venda in vendas_filtradas:
        try:
            itens = json.loads(venda.produtos_vendidos) if isinstance(venda.produtos_vendidos, str) else (venda.produtos_vendidos or [])
        except Exception:
            itens = []

        for item in itens:
            nome_prod = item.get("produto") or item.get("nome")
            qtd = int(item.get("quantidade", 0))
            preco_venda_item = float(item.get("preco_unitario", 0.0) or item.get("preco", 0.0))
            prod = Produto.query.filter_by(usuario_id=id_logado, nome=nome_prod).first()
            if prod and prod.custo and float(prod.custo) > 0:
                lucro_vendas += (preco_venda_item - float(prod.custo)) * qtd

    lucro_total = lucro_vendas + lucro_trocas

    return render_template(
        "relatorio_financeiro.html",
        total=f"{total_geral:.2f}",
        pix=f"{pix:.2f}",
        dinheiro=f"{dinheiro:.2f}",
        cartao=f"{cartao:.2f}",
        lucro=f"{lucro_total:.2f}",
        filtro=filtro
    )

@app.route("/relatorio-caixa")
def relatorio_caixa():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    caixas_db = Caixa.query.filter_by(usuario_id=id_logado).order_by(Caixa.id.desc()).limit(100).all()

    historico_caixa = []
    for c in caixas_db:
        inicial = c.valor_inicial or 0.0
        final = c.saldo_final or 0.0

        val_vendas = getattr(c, 'vendas_periodo', None) or getattr(c, 'total_vendas', None) or getattr(c, 'vendas', 0.0)

        if (not val_vendas or val_vendas == 0) and final > 0:
            val_vendas = max(0.0, final - inicial)

        abertura = c.data_abertura.strftime("%d/%m/%Y %H:%M") if hasattr(c.data_abertura, 'strftime') else (c.data_abertura or "N/A")
        
        fechamento = "Em Aberto 🟢"
        if c.data_fechamento and str(c.data_fechamento) != "None":
            fechamento = c.data_fechamento.strftime("%d/%m/%Y %H:%M") if hasattr(c.data_fechamento, 'strftime') else c.data_fechamento

        historico_caixa.append({
            "id": c.id,  # 👈 Passando o ID para acionar a impressão
            "data_abertura": abertura,
            "data_fechamento": fechamento,
            "valor_inicial": inicial,
            "vendas": val_vendas,
            "saldo_final": final
        })

    return render_template(
        "relatorio_caixa.html",
        historico_caixa=historico_caixa
    )


@app.route("/imprimir-caixa/<int:caixa_id>")
def imprimir_caixa(caixa_id):
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    caixa = Caixa.query.filter_by(id=caixa_id, usuario_id=id_logado).first_or_404()

    inicial = caixa.valor_inicial or 0.0
    vendas = getattr(caixa, 'vendas_periodo', None) or getattr(caixa, 'total_vendas', None) or getattr(caixa, 'vendas', 0.0)
    final = caixa.saldo_final or (inicial + vendas)

    abertura = caixa.data_abertura.strftime("%d/%m/%Y %H:%M") if hasattr(caixa.data_abertura, 'strftime') else (caixa.data_abertura or "N/A")
    fechamento = caixa.data_fechamento.strftime("%d/%m/%Y %H:%M") if hasattr(caixa.data_fechamento, 'strftime') else (caixa.data_fechamento or "Em Aberto")

    dados_relatorio = {
        "id": caixa.id,
        "abertura": abertura,
        "fechamento": fechamento,
        "valor_inicial": f"{inicial:.2f}",
        "vendas": f"{vendas:.2f}",
        "saldo_final": f"{final:.2f}"
    }

    return render_template("imprimir_caixa.html", caixa=dados_relatorio)

@app.route("/relatorio-graficos")
@apenas_admin
def relatorio_graficos():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    filtro = request.args.get("filtro", "semana")
    hoje = datetime.now()

    todas_vendas = Venda.query.filter_by(usuario_id=id_logado).all()
    vendas_db = [v for v in todas_vendas if getattr(v, 'status', '') != 'CANCELADA']

    # 1. Meios de pagamento das Vendas Padrão
    pix, dinheiro, cartao = processar_totais_pagamento(vendas_db)

    # 2. ➕ Adiciona recebimentos vindos de Diferenças de Trocas
    todas_trocas = Troca.query.filter_by(usuario_id=id_logado).all()
    for troca in todas_trocas:
        if getattr(troca, 'status', '') == 'CANCELADA':
            continue

        saldo = float(getattr(troca, 'saldo_diferenca', 0) or 0)
        
        # Considera apenas quando o cliente pagou a diferença (saldo < 0)
        if saldo < 0:
            valor_recebido = abs(saldo)
            forma = str(getattr(troca, 'forma_pagamento_diferenca', '') or getattr(troca, 'forma_pagamento', '')).strip()

            # Processa pagamento Misto formatado na string
            if "MISTO" in forma.upper():
                partes = forma.split("(")[-1].replace(")", "").split("|")
                for parte in partes:
                    if ":" in parte:
                        nome_f, val_f = parte.split(":", 1)
                        nome_upper = nome_f.upper()
                        try:
                            val_num = float(val_f.replace("R$", "").replace(",", ".").strip())
                        except ValueError:
                            val_num = 0.0
                        
                        if "DINHEIRO" in nome_upper:
                            dinheiro += val_num
                        elif "PIX" in nome_upper:
                            pix += val_num
                        elif "CART" in nome_upper:
                            cartao += val_num
            else:
                # Pagamento único
                f_upper = forma.upper()
                if "DINHEIRO" in f_upper:
                    dinheiro += valor_recebido
                elif "PIX" in f_upper:
                    pix += valor_recebido
                elif "CART" in f_upper:
                    cartao += valor_recebido

    vendas_por_periodo = defaultdict(float)

    for venda in vendas_db:
        try:
            data_venda = datetime.strptime(venda.data, "%d/%m/%Y %H:%M")
        except Exception:
            continue

        val_venda = getattr(venda, 'valor_total', 0) or getattr(venda, 'total', 0)

        if filtro == "semana":
            if data_venda.isocalendar()[1] == hoje.isocalendar()[1] and data_venda.year == hoje.year:
                chave = data_venda.strftime("%d/%m")
                vendas_por_periodo[chave] += val_venda
        elif filtro == "mes":
            if data_venda.month == hoje.month and data_venda.year == hoje.year:
                chave = data_venda.strftime("%d")
                vendas_por_periodo[chave] += val_venda
        elif filtro == "ano":
            if data_venda.year == hoje.year:
                meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
                chave = meses[data_venda.month - 1]
                vendas_por_periodo[chave] += val_venda

    ordem_meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

    if filtro == "ano":
        datas = [mes for mes in ordem_meses if mes in vendas_por_periodo]
        valores = [vendas_por_periodo[mes] for mes in datas]
    else:
        datas = sorted(vendas_por_periodo.keys())
        valores = [vendas_por_periodo[data] for data in datas]

    dias_semana = {"Seg": 0.0, "Ter": 0.0, "Qua": 0.0, "Qui": 0.0, "Sex": 0.0, "Sáb": 0.0, "Dom": 0.0}
    semana_atual = hoje.isocalendar()[1]

    for venda in vendas_db:
        try:
            data_venda = datetime.strptime(venda.data, "%d/%m/%Y %H:%M")
        except Exception:
            continue

        if data_venda.isocalendar()[1] == semana_atual and data_venda.year == hoje.year:
            dia = data_venda.weekday()
            dias_nomes = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
            val_venda = getattr(venda, 'valor_total', 0) or getattr(venda, 'total', 0)
            dias_semana[dias_nomes[dia]] += val_venda

    return render_template(
        "relatorio_graficos.html",
        pix=round(pix, 2),
        dinheiro=round(dinheiro, 2),
        cartao=round(cartao, 2),
        datas=datas,
        valores=valores,
        filtro=filtro,
        dias_semana=list(dias_semana.keys()),
        valores_semana=list(dias_semana.values())
    )


@app.route("/relatorio-produtos")
@apenas_admin
def relatorio_produtos():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    vendas_db = Venda.query.filter_by(usuario_id=id_logado).all()

    produtos_vendidos = {}
    produtos_lucro = {}

    for venda in vendas_db:
        # 🚫 Ignora produtos de vendas canceladas
        if getattr(venda, 'status', '') == 'CANCELADA':
            continue

        try:
            itens = json.loads(venda.produtos_vendidos) if isinstance(venda.produtos_vendidos, str) else venda.produtos_vendidos
        except:
            itens = []

        for item in itens:
            nome = item.get("produto") or item.get("nome")
            quantidade = item.get("quantidade", 0)
            preco_venda_item = item.get("preco_unitario", 0.0)

            if not nome:
                continue

            produtos_vendidos[nome] = produtos_vendidos.get(nome, 0) + quantidade

            prod = Produto.query.filter_by(usuario_id=id_logado, nome=nome).first()
            if prod and prod.custo and prod.custo > 0:
                lucro_unitario = preco_venda_item - prod.custo
                lucro_total_item = lucro_unitario * quantidade
            else:
                lucro_total_item = 0.0

            produtos_lucro[nome] = produtos_lucro.get(nome, 0) + lucro_total_item

    top_vendidos = sorted(produtos_vendidos.items(), key=lambda x: x[1], reverse=True)[:10]
    top_lucro = sorted(produtos_lucro.items(), key=lambda x: x[1], reverse=True)[:10]

    nomes_vendidos = [item[0] for item in top_vendidos]
    quantidades_vendidas = [item[1] for item in top_vendidos]

    nomes_lucro = [item[0] for item in top_lucro]
    valores_lucro = [item[1] for item in top_lucro]

    return render_template(
        "relatorio_produtos.html",
        nomes_vendidos=nomes_vendidos,
        quantidades_vendidas=quantidades_vendidas,
        nomes_lucro=nomes_lucro,
        valores_lucro=valores_lucro
    )

@app.route("/nova-troca")
def nova_troca():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Busca os produtos cadastrados no SQLite para este usuário logado, ordenados por nome
    produtos_ordenados = Produto.query.filter_by(usuario_id=id_logado).order_by(Produto.nome.asc()).all()

    return render_template(
        "nova_troca.html",
        produtos=produtos_ordenados
    )


@app.route("/finalizar-troca", methods=["POST"])
def finalizar_troca():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return jsonify({"erro": "❌ Usuário não autenticado"}), 401

    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
    if not caixa_atual:
        return jsonify({"erro": "❌ Abra o caixa antes de realizar ou finalizar uma troca."}), 400

    data = request.get_json()
    devolvidos = data.get("devolvidos", [])
    novos = data.get("novosProdutos", [])
    credito = float(data.get("credito", 0))
    total_compra = float(data.get("totalCompra", 0))
    abrir_mao = data.get("abrirMaoCredito", False)
    forma_pagamento = data.get("formaPagamento", "Troca Sem Diferença")
    parcelas = int(data.get("parcelas", 1))

    diferenca = credito - total_compra
    
    if diferenca > 0 and not abrir_mao:
        return jsonify({"erro": "❌ Não é permitido finalizar trocas com saldo restante sem o cliente abrir mão da diferença."}), 400

    if diferenca > 0 and abrir_mao:
        saldo_salvar = 0.0
    else:
        saldo_salvar = diferenca

    # 1. Devolve itens ao estoque
    for item in devolvidos:
        produto = Produto.query.filter_by(id=int(item["id"]), usuario_id=id_logado).first()
        if produto:
            produto.estoque += int(item["quantidade"])

    # 2. Retira novos itens do estoque
    for item in novos:
        produto = Produto.query.filter_by(id=int(item["id"]), usuario_id=id_logado).first()
        if produto:
            produto.estoque -= int(item["quantidade"])

    # 3. Entrada financeira no caixa se o cliente comprou MAIS (diferença negativa)
    valor_pago_restante = abs(diferenca) if diferenca < 0 else 0
    if diferenca < 0:
        caixa_atual.vendas_periodo += valor_pago_restante

    # 4. Formatação amigável para exibição em caso de pagamento misto
    if forma_pagamento.startswith("["):
        try:
            lista_p = json.loads(forma_pagamento)
            texto_p = " | ".join([f"{p['forma']}: R$ {p['valor']:.2f}" for p in lista_p])
            forma_pagamento_salvar = f"Misto ({texto_p})"
        except Exception:
            forma_pagamento_salvar = forma_pagamento
    else:
        forma_pagamento_salvar = forma_pagamento

    # 5. Gravação no banco com os novos campos
    data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M")

    nova_troca_db = Troca(
        usuario_id=id_logado,
        data=data_hoje,
        produtos_devolvidos=json.dumps(devolvidos),
        produtos_recebidos=json.dumps(novos),
        credito=credito,
        total_compra=total_compra,
        saldo_diferenca=saldo_salvar,
        forma_pagamento_diferenca=forma_pagamento_salvar if diferenca < 0 else "Troca Sem Diferença",
        parcelas=parcelas if diferenca < 0 else 1
    )
    
    db.session.add(nova_troca_db)
    db.session.commit()

    return jsonify({"mensagem": "✅ Troca finalizada com sucesso!"})

@app.route("/lucro")
@apenas_admin
def lucro():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    lucro_total = 0
    produtos_com_custo = []
    produtos_sem_custo = []

    # 1. Busca todos os produtos do usuário logado no SQLite para separar com/sem custo
    todos_produtos = Produto.query.filter_by(usuario_id=id_logado).all()
    for produto in todos_produtos:
        custo_val = produto.custo or 0.0
        
        prod_dict = {
            "id": produto.id,
            "nome": produto.nome,
            "preco": produto.preco,
            "custo": custo_val,
            "estoque": produto.estoque
        }
        
        if custo_val > 0:
            produtos_com_custo.append(prod_dict)
        else:
            produtos_sem_custo.append(prod_dict)

    # 2. Busca todas as vendas do usuário no SQLite
    vendas_db = Venda.query.filter_by(usuario_id=id_logado).all()
    for venda in vendas_db:
        
        # 🚫 IGNORA VENDAS CANCELADAS NO CÁLCULO DO LUCRO
        if getattr(venda, 'status', '') == 'CANCELADA':
            continue

        try:
            itens = json.loads(venda.produtos_vendidos) if isinstance(venda.produtos_vendidos, str) else venda.produtos_vendidos
        except:
            itens = []

        for item in itens:
            nome_produto = item.get("produto") or item.get("nome")
            quantidade = item.get("quantidade", 0)
            preco_venda = item.get("preco_unitario", 0.0)

            # Procura o produto no banco para pegar o custo cadastrado dele
            prod_banco = Produto.query.filter_by(usuario_id=id_logado, nome=nome_produto).first()
            preco_custo = prod_banco.custo if (prod_banco and prod_banco.custo) else 0.0

            # 🌟 TRAVA DE SEGURANÇA: Só calcula o lucro se o produto tiver um custo cadastrado (maior que zero)
            if preco_custo > 0:
                lucro_item = (preco_venda - preco_custo) * quantidade
                lucro_total += lucro_item

    return render_template(
        "lucro.html",
        lucro_total=round(lucro_total, 2),
        produtos_com_custo=produtos_com_custo,
        produtos_sem_custo=produtos_sem_custo
    )





# 1. DECLARE A LISTA AQUI (FORA E ANTES DA FUNÇÃO)
despesas_db = []

@app.route('/despesas', methods=['GET', 'POST'])
def despesas():
    global despesas_db, caixa
    
    if request.method == 'POST':
        descricao = request.form.get('descricao')
        valor = float(request.form.get('valor', 0))
        categoria = request.form.get('categoria')
        origem_pagamento = request.form.get('origem_pagamento')
        
        nova_despesa = {
            'id': len(despesas_db) + 1,
            'descricao': descricao,
            'valor': valor,
            'categoria': categoria,
            'origem': origem_pagamento,
            'data': datetime.now().strftime('%d/%m/%Y %H:%M')
        }
        
        despesas_db.append(nova_despesa)
        
        # 🟢 ABATE DIRETO NO CAIXA DA LOJA (SANGRIA)
        if origem_pagamento == 'caixa' and 'caixa' in globals() and isinstance(caixa, dict):
            if caixa.get('aberto'):
                # Garante que o campo de despesas do caixa exista
                if 'despesas' not in caixa:
                    caixa['despesas'] = 0.0
                
                # Registra o valor retirado
                caixa['despesas'] += valor

        flash('Despesa registrada com sucesso!', 'sucesso')
        return redirect(url_for('despesas'))

    total_despesas = sum(d['valor'] for d in despesas_db)
    return render_template('despesas.html', despesas=despesas_db, total_despesas=total_despesas)



@app.route("/gestao")
@apenas_admin
def gestao():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    from sqlalchemy import func
    produtos_db = Produto.query.filter_by(usuario_id=id_logado).order_by(func.lower(Produto.nome)).all()

    produtos_ordenados = []
    for produto in produtos_db:
        # Pega a propriedade correta do modelo (estoque_minimo, minimo ou estoque_min)
        minimo_val = getattr(produto, 'estoque_minimo', None)
        if minimo_val is None:
            minimo_val = getattr(produto, 'minimo', 0)

        # 🌟 Trava de segurança: garante que o estoque nunca seja menor que 0 na exibição
        estoque_exibicao = max(0, produto.estoque or 0)

        produtos_ordenados.append({
            "id": produto.id,
            "nome": produto.nome,
            "preco": produto.preco,
            "custo": produto.custo or 0.0,
            "estoque": estoque_exibicao,
            "estoque_minimo": minimo_val or 0
        })

    return render_template(
        "gestao.html",
        produtos=produtos_ordenados
    )


@app.route("/estoque")
def estoque():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Busca todos os produtos do usuário logado no SQLite
    produtos = Produto.query.filter_by(usuario_id=id_logado).order_by(Produto.nome.asc()).all()

    return render_template("estoque.html", produtos=produtos)


@app.route("/criar-estoque")
def criar_estoque():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Busca todos os produtos do usuário logado no SQLite
    produtos = Produto.query.filter_by(usuario_id=id_logado).order_by(Produto.nome.asc()).all()

    return render_template("criar_estoque.html", produtos=produtos)

@app.route("/salvar-estoque", methods=["POST"])
def salvar_estoque():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    try:
        produto_id = int(request.form.get("produto"))
        estoque = int(request.form.get("estoque"))
        estoque_minimo = int(request.form.get("estoque_minimo") or 0)
    except (ValueError, TypeError):
        return "❌ Valores numéricos inválidos.", 400

    # Busca o produto do usuário logado no banco
    produto = Produto.query.filter_by(id=produto_id, usuario_id=id_logado).first()

    if produto:
        produto.estoque = estoque
        produto.estoque_minimo = estoque_minimo  # 🌟 ATIVADO! Agora salva de verdade no SQLite
        db.session.commit()
    else:
        return "❌ Produto não encontrado.", 404

    return redirect("/estoque")

@app.route("/editar-gestao/<int:id>")
@app.route("/editar-gestao", methods=["GET"])
def editar_gestao(id=None):
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Se o ID não veio pela URL (ex: /editar-gestao?id=3), tenta pegar pelo parâmetro de busca
    if id is None:
        id_busca = request.args.get("id")
        if id_busca:
            id = int(id_busca)
        else:
            # Caso o botão principal lá de cima "Adicionar / Atualizar Custos" tenha sido clicado sem ID
            return redirect("/gestao")

    # Busca o produto no SQLite
    produto = Produto.query.filter_by(id=id, usuario_id=id_logado).first()

    if not produto:
        return "❌ Produto não encontrado.", 404

    # Converte para dicionário para o template renderizar perfeitamente
    produto_formatado = {
        "id": produto.id,
        "nome": produto.nome,
        "preco": produto.preco,
        "custo": produto.custo or 0.0,
        "estoque": produto.estoque,
        "estoque_minimo": produto.estoque_minimo or 0
    }

    return render_template("editar_gestao.html", produto=produto_formatado)


@app.route("/salvar-gestao/<int:id>", methods=["POST"])
def salvar_gestao(id):
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Busca o produto do usuário logado no banco
    produto = Produto.query.filter_by(id=id, usuario_id=id_logado).first()

    if produto:
        try:
            produto.preco = float(request.form.get("preco"))
            produto.custo = float(request.form.get("custo") or 0.0)
            produto.estoque = int(request.form.get("estoque") or 0)
            
            # 🌟 ATIVADO: Agora também salva e atualiza o estoque mínimo de forma profissional!
            produto.estoque_minimo = int(request.form.get("estoque_minimo") or 0)
            
        except (ValueError, TypeError):
            return "❌ Valores numéricos inválidos.", 400

        # Aplica todas as alterações de uma vez no banco de dados
        db.session.commit()

    return redirect("/gestao")


from datetime import datetime
@app.route("/caixa")
def caixa():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Busca o último caixa registrado
    ultimo_caixa = Caixa.query.filter_by(usuario_id=id_logado).order_by(Caixa.id.desc()).first()

    total_despesas_caixa = sum(
        d['valor'] for d in despesas_db if d.get('origem') == 'caixa'
    )

    # Considera fechado se não existir ou se tiver data de fechamento / aberto == False
    if not ultimo_caixa or ultimo_caixa.data_fechamento or not ultimo_caixa.aberto:
        caixa_formatado = {
            "aberto": False,
            "valor_inicial": 0.0,
            "vendas_periodo": 0.0,
            "despesas": 0.0,
            "saldo_atual": 0.0,
            "data_abertura": "",
            "operador": session.get("usuario_nome", "Ingrid"),
            "dispositivo": "D1",
            "recebimentos": 0.0,
            "taxa_servico": 0.0,
            "vendas_fiado": 0.0,
            "vendas_por_forma": {},
            "detalhes_formas": []
        }
    else:
        vendas_formas = {
            "DINHEIRO": 0.0,
            "DÉBITO": 0.0,
            "CRÉDITO": 0.0,
            "CRÉDITO PARCELADO": 0.0,
            "PIX": 0.0
        }

        try:
            vendas = Venda.query.filter_by(usuario_id=id_logado, caixa_id=ultimo_caixa.id).all() 
            
            for v in vendas:
                pagamento_dado = getattr(v, 'pagamento', '')
                total_venda = float(getattr(v, 'valor_total', 0.0))

                if pagamento_dado and pagamento_dado.startswith('['):
                    try:
                        lista_pags = json.loads(pagamento_dado)
                        for item in lista_pags:
                            tipo = str(item.get('tipo', '')).upper()
                            vlr = float(item.get('valor', 0.0))
                            
                            if "DINHEIRO" in tipo: vendas_formas["DINHEIRO"] += vlr
                            elif "DÉBITO" in tipo or "DEBITO" in tipo: vendas_formas["DÉBITO"] += vlr
                            elif "PARCELADO" in tipo: vendas_formas["CRÉDITO PARCELADO"] += vlr
                            elif "CRÉDITO" in tipo or "CREDITO" in tipo: vendas_formas["CRÉDITO"] += vlr
                            elif "PIX" in tipo: vendas_formas["PIX"] += vlr
                            else: vendas_formas["DINHEIRO"] += vlr
                        continue
                    except Exception:
                        pass

                forma = str(pagamento_dado).upper()
                if "DINHEIRO" in forma:
                    vendas_formas["DINHEIRO"] += total_venda
                elif "DÉBITO" in forma or "DEBITO" in forma:
                    vendas_formas["DÉBITO"] += total_venda
                elif "PARCELADO" in forma:
                    vendas_formas["CRÉDITO PARCELADO"] += total_venda
                elif "CRÉDITO" in forma or "CREDITO" in forma:
                    vendas_formas["CRÉDITO"] += total_venda
                elif "PIX" in forma:
                    vendas_formas["PIX"] += total_venda
                else:
                    vendas_formas["DINHEIRO"] += total_venda

        except Exception as e:
            print(f"❌ Erro ao calcular formas de pagamento: {e}")
            vendas_formas["DINHEIRO"] = ultimo_caixa.vendas_periodo

        detalhes_formas = [
            {
                "nome": "Dinheiro",
                "entrada": ultimo_caixa.valor_inicial + vendas_formas["DINHEIRO"],
                "saida": total_despesas_caixa,
                "saldo": (ultimo_caixa.valor_inicial + vendas_formas["DINHEIRO"]) - total_despesas_caixa
            },
            {"nome": "Débito", "entrada": vendas_formas["DÉBITO"], "saida": 0.0, "saldo": vendas_formas["DÉBITO"]},
            {"nome": "Crédito", "entrada": vendas_formas["CRÉDITO"], "saida": 0.0, "saldo": vendas_formas["CRÉDITO"]},
            {"nome": "Crédito Parcelado", "entrada": vendas_formas["CRÉDITO PARCELADO"], "saida": 0.0, "saldo": vendas_formas["CRÉDITO PARCELADO"]},
            {"nome": "Pix", "entrada": vendas_formas["PIX"], "saida": 0.0, "saldo": vendas_formas["PIX"]}
        ]

        saldo_calculado = (ultimo_caixa.valor_inicial + ultimo_caixa.vendas_periodo) - total_despesas_caixa

        caixa_formatado = {
            "aberto": True,
            "valor_inicial": ultimo_caixa.valor_inicial,
            "vendas_periodo": ultimo_caixa.vendas_periodo,
            "despesas": total_despesas_caixa,
            "saldo_atual": saldo_calculado,
            "data_abertura": ultimo_caixa.data_abertura,
            "operador": session.get("usuario_nome", "Ingrid"),
            "dispositivo": "D1",
            "recebimentos": 0.0,
            "taxa_servico": 0.0,
            "vendas_fiado": 0.0,
            "vendas_por_forma": vendas_formas,
            "detalhes_formas": detalhes_formas
        }

    data_br = datetime.now(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")

    return render_template(
        "caixa.html", 
        caixa=caixa_formatado, 
        data_atual=data_br
    )


# ==========================================
# 3. ROTA DE ABRIR CAIXA
# ==========================================
@app.route("/abrir-caixa", methods=["POST"])
def abrir_caixa():
    global despesas_db
    
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    valor = float(request.form.get("valor_inicial", 0.0))
    data_br = datetime.now(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")

    novo_caixa = Caixa(
        usuario_id=id_logado,
        aberto=True,
        valor_inicial=valor,
        vendas_periodo=0.0,
        saldo_final=0.0,
        data_abertura=data_br
    )

    db.session.add(novo_caixa)
    db.session.commit()

    # Zera as despesas para o novo turno
    despesas_db.clear()

    flash("🔓 Caixa aberto com sucesso!", "success")
    print(f"💰 CAIXA ABERTO COM R$ {valor} PARA O USUÁRIO {id_logado}")
    return redirect("/caixa")


# ==========================================
# 4. ROTA DE FECHAR CAIXA
# ==========================================
@app.route("/fechar-caixa", methods=["POST"])
def fechar_caixa():
    global despesas_db
    
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True, data_fechamento=None).order_by(Caixa.id.desc()).first()

    if caixa_atual:
        total_despesas_caixa = sum(d['valor'] for d in despesas_db if d.get('origem') == 'caixa')
        saldo_final = (caixa_atual.valor_inicial + caixa_atual.vendas_periodo) - total_despesas_caixa
        
        caixa_atual.aberto = False
        caixa_atual.saldo_final = saldo_final
        caixa_atual.data_fechamento = datetime.now(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")
        
        db.session.commit()
        
        flash("🔒 Caixa fechado com sucesso!", "success") 
        print(f"🔒 CAIXA FECHADO COM SUCESSO. SALDO FINAL: R$ {saldo_final}")

    return redirect("/caixa")


# =========================================================
# INICIALIZAÇÃO DO BANCO & MIGRAÇÕES (RODA NO RENDER E LOCAL)
# =========================================================
with app.app_context():
    db.create_all()

    from sqlalchemy import text
    from werkzeug.security import generate_password_hash
    from datetime import datetime, timedelta

    # 1. Migrações da tabela 'venda' (Compatível com SQLite e Render)
    try:
        db.session.execute(text("ALTER TABLE venda ADD COLUMN status VARCHAR(50) DEFAULT 'concluida';"))
        db.session.commit()
        print("✅ Coluna 'status' verificada/adicionada com sucesso!")
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE venda ADD COLUMN motivo_cancelamento TEXT;"))
        db.session.commit()
        print("✅ Coluna 'motivo_cancelamento' verificada/adicionada com sucesso!")
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE venda ALTER COLUMN pagamento TYPE TEXT;"))
        db.session.commit()
        print("✅ Coluna 'pagamento' alterada para TEXT com sucesso!")
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE venda ADD COLUMN desconto FLOAT DEFAULT 0.0;"))
        db.session.commit()
        print("✅ Coluna 'desconto' verificada/adicionada com sucesso!")
    except Exception:
        db.session.rollback()

    # 2. Migrações da tabela 'troca' (🌟 NOVOS CAMPOS ADICIONADOS AQUI)
    try:
        db.session.execute(text("ALTER TABLE troca ADD COLUMN forma_pagamento_diferenca VARCHAR(50);"))
        db.session.commit()
        print("✅ Coluna 'forma_pagamento_diferenca' em 'troca' verificada/adicionada!")
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE troca ADD COLUMN parcelas INTEGER DEFAULT 1;"))
        db.session.commit()
        print("✅ Coluna 'parcelas' em 'troca' verificada/adicionada!")
    except Exception:
        db.session.rollback()

    # 3. Criador automático de Admin
    seu_email_real = "igordesouzacordeiro18@gmail.com"
    sua_senha_real = "123123"

    admin_antigo = Usuario.query.filter_by(email="admin@teste.com").first()
    if admin_antigo:
        db.session.delete(admin_antigo)
        db.session.commit()

    if not Usuario.query.filter_by(email=seu_email_real).first():
        admin = Usuario(
            email=seu_email_real, 
            senha=generate_password_hash(sua_senha_real), 
            primeiro_acesso=False, 
            status="ativo",
            validade_plano=datetime.now() + timedelta(days=365)
        )
        db.session.add(admin)
        db.session.commit()
        print(f"🚀 SEU USUÁRIO FOI CRIADO COM SUCESSO: {seu_email_real}")


# Executado apenas ao rodar localmente via linha de comando
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)