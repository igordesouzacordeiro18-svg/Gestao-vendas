import json
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, jsonify, url_for, flash, session
from collections import defaultdict
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from functools import wraps

app = Flask(__name__)

# === CONFIGURAÇÕES DE SEGURANÇA E BANCO DE DADOS ===
# Chave secreta puxada do ambiente ou valor padrão
app.secret_key = os.getenv("SECRET_KEY", "uma_chave_criptografica_muito_segura_aqui")

# Na nuvem usará a URL do PostgreSQL, localmente usará o SQLite
db_uri = os.getenv("DATABASE_URL", "sqlite:///sistema.db")
# Ajuste de compatibilidade caso o Render forneça a URL começando com "postgres://"
if db_uri.startswith("postgres://"):
    db_uri = db_uri.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializa o banco de dados no seu app Flask
db = SQLAlchemy(app)


# =======================================================
# CONFIGURAÇÃO DE E-MAIL (SERVIDORES SMTP GMAIL)
# =======================================================
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME", "igordesouzacordeiro18@gmail.com")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD", "wazo vmkl jdkk rguf")

default_sender_email = os.getenv("MAIL_USERNAME", "igordesouzacordeiro18@gmail.com")
app.config['MAIL_DEFAULT_SENDER'] = ('Suporte Sistema', default_sender_email)

mail = Mail(app)
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
    caixa_id = db.Column(db.Integer, db.ForeignKey('caixa.id'), nullable=True) # Vincula ao caixa atual!
    
    valor_total = db.Column(db.Float, nullable=False)
    data = db.Column(db.String(20)) # Ex: "09/07/2026 14:15"
    produtos_vendidos = db.Column(db.Text, nullable=False) # Itens da venda em JSON
    pagamento = db.Column(db.String(50), nullable=True) # 🌟 NOVA COLUNA PROFISSIONAL!

class Troca(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False) # Garanta que a FK aponte para sua tabela de usuários (ex: usuario.id ou user.id)
    data = db.Column(db.String(20), nullable=False)
    produtos_devolvidos = db.Column(db.Text, nullable=False)  # Armazena a lista de devolvidos como string JSON
    produtos_recebidos = db.Column(db.Text, nullable=False)    # Armazena a lista de novos como string JSON
    credito = db.Column(db.Float, nullable=False)
    total_compra = db.Column(db.Float, nullable=False)
    saldo_diferenca = db.Column(db.Float, nullable=False)


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
                
                # Gera o link dinâmico completo apontando para o Render
                link_redefinicao = url_for("redefinir_senha_token", token=token, _external=True)

                msg = Message("🔒 Recuperação de Senha - Sistema", recipients=[email_digitado])
                msg.body = f"""Olá!

Recebemos uma solicitação para redefinir a senha da sua conta no sistema.

Para criar uma nova senha, clique no link abaixo (válido por 15 minutos):
{link_redefinicao}

Se você não solicitou essa alteração, ignore este e-mail.
"""
                mail.send(msg)
                print(f"✅ E-mail de recuperação enviado para: {email_digitado}")

        except Exception as e:
            # Imprime o motivo real do erro nos Logs do Render
            print(f"❌ ERRO NO ENVIO DE E-MAIL: {type(e).__name__} - {e}")
            flash("Ocorreu uma falha ao tentar enviar o e-mail de recuperação. Verifique os servidores SMTP.")
            return redirect(url_for("esqueci_senha"))

        flash("Se o e-mail estiver cadastrado em nosso sistema, você receberá as instruções de redefinição em instantes.")
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
        nova_senha = request.form.get("nova_senha")
        confirmar_senha = request.form.get("confirmar_senha")

        if nova_senha and confirmar_senha:
            if nova_senha != confirmar_senha:
                return render_template("redefinir_senha_token.html", token=token, erro="As senhas não coincidem.")

            usuario = Usuario.query.filter_by(email=email).first()
            if usuario:
                usuario.senha = generate_password_hash(nova_senha)
                usuario.primeiro_acesso = False
                db.session.commit()

                flash("✅ Sua senha foi alterada com sucesso! Faça login com a nova senha.")
                return redirect(url_for("login"))

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

    # 2. Busca o caixa atual do usuário
    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
    
    total_vendas_periodo = 0
    valor_total_caixa = 0
    ultimas_vendas_lista = []

    if caixa_atual:
        # Busca todas as vendas que pertencem ao caixa que está aberto atualmente
        vendas_caixa = Venda.query.filter_by(usuario_id=id_logado, caixa_id=caixa_atual.id).all()
        total_vendas_periodo = len(vendas_caixa)
        valor_total_caixa = caixa_atual.valor_inicial + caixa_atual.vendas_periodo

        # Pega as últimas 5 vendas do período atual (da mais recente para a mais antiga)
        for v in reversed(vendas_caixa):
            if len(ultimas_vendas_lista) < 5:
                # Carrega os produtos vendidos que salvamos em texto JSON
                try:
                    itens = json.loads(v.produtos_vendidos)
                except:
                    itens = []
                
                ultimas_vendas_lista.append({
                    "id": v.id,
                    "total": v.valor_total,
                    "data": v.data,
                    "itens": itens
                })

    # 3. Estatísticas Gerais de todas as vendas do usuário (para os gráficos)
    todas_vendas = Venda.query.filter_by(usuario_id=id_logado).all()
    
    contador_produtos = {}
    total_pix = 0
    total_cartao = 0
    total_dinheiro = 0

    for venda in todas_vendas:
        # No SQLite, guardamos os produtos vendidos como String de JSON. Vamos decodificar:
        try:
            itens = json.loads(venda.produtos_vendidos)
        except:
            itens = []

        for item in itens:
            nome_p = item.get("produto")
            qtd = item.get("quantidade", 0)
            if nome_p:
                contador_produtos[nome_p] = contador_produtos.get(nome_p, 0) + qtd

        # Classificação básica de pagamentos para os cards do dashboard
        # (Se quiser melhorar no futuro salvando a forma de pagamento na tabela Venda, já temos a lógica engatilhada!)
        total_dinheiro += venda.valor_total # Por enquanto, soma tudo como dinheiro se não houver campo específico

    produto_mais_vendido = "Nenhum"
    if contador_produtos:
        produto_mais_vendido = max(contador_produtos, key=contador_produtos.get)

    return render_template(
        "dashboard.html",
        total_vendas=total_vendas_periodo,
        total_produtos=total_produtos,
        valor_total=valor_total_caixa,
        ultimas_vendas=ultimas_vendas_lista,
        produto_mais_vendido=produto_mais_vendido,
        pix=total_pix,
        cartao=total_cartao,
        dinheiro=total_dinheiro
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
        return "❌ Usuário não autenticado", 401

    # 1. Verifica se o caixa está aberto no SQLite
    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
    if not caixa_atual:
        return """
        <script>
            alert('❌ Abra o caixa antes de realizar uma venda.');
            window.location.href='/caixa';
        </script>
        """

    # 2. Carrega o carrinho enviado pelo front-end
    carrinho_json = request.form.get("carrinho")
    if not carrinho_json:
        return "❌ Carrinho vazio"

    try:
        carrinho = json.loads(carrinho_json)
    except json.JSONDecodeError:
        return "❌ Erro ao processar os itens do carrinho.", 400

    pagamento1 = request.form.get("pagamento1")
    valor1 = request.form.get("valor1")
    pagamento2 = request.form.get("pagamento2")
    valor2 = request.form.get("valor2")
    valor_pago_cliente = float(request.form.get("valor_pago") or 0)

    # Organiza os pagamentos de forma detalhada
    pagamentos = []
    if pagamento1:
        pagamentos.append({"tipo": pagamento1, "valor": float(valor1 or 0)})
    if pagamento2:
        pagamentos.append({"tipo": pagamento2, "valor": float(valor2 or 0)})

    # SE FOR PAGAMENTO MISTO: Grava o JSON dos pagamentos no campo
    # SE FOR PAGAMENTO ÚNICO: Grava apenas a string (ex: "Dinheiro", "Pix")
    if len(pagamentos) > 1:
        pagamento_str = json.dumps(pagamentos, ensure_ascii=False)
    elif len(pagamentos) == 1:
        pagamento_str = pagamentos[0]["tipo"]
    else:
        pagamento_str = "Dinheiro"

    total_geral = 0
    itens_vendidos_lista = []

    # 3. Processa cada item do carrinho
    for item in carrinho:
        nome_produto = item["nome"].split(" - R$")[0].strip()
        produto = Produto.query.filter_by(usuario_id=id_logado, nome=nome_produto).first()

        if not produto:
            return f"❌ Produto não encontrado no banco: {nome_produto}", 404

        if item["quantidade"] >= produto.estoque:
            produto.estoque = 0
        else:
            produto.estoque -= item["quantidade"]

        subtotal = produto.preco * item["quantidade"]
        total_geral += subtotal

        itens_vendidos_lista.append({
            "produto": produto.nome,
            "quantidade": item["quantidade"],
            "preco_unitario": produto.preco,
            "subtotal": subtotal
        })

    # 4. Registra a nova Venda na tabela
    nova_venda_db = Venda(
        usuario_id=id_logado,
        caixa_id=caixa_atual.id,
        valor_total=total_geral,
        data=datetime.now().strftime("%d/%m/%Y %H:%M"),
        produtos_vendidos=json.dumps(itens_vendidos_lista, ensure_ascii=False),
        pagamento=pagamento_str  # <--- Guarda a quebra exata do pagamento aqui!
    )

    # 5. Atualiza o faturamento do Caixa
    caixa_atual.vendas_periodo += total_geral

    db.session.add(nova_venda_db)
    db.session.commit()

    print(f"💰 VENDA REGISTRADA NO SQLITE! Total: R$ {total_geral:.2f}")
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

    # 1. Busca os produtos do usuário logado direto do SQLite (ordenados por nome)
    produtos_ordenados = Produto.query.filter_by(usuario_id=id_logado).order_by(Produto.nome.asc()).all()

    # 2. 🌟 CORREÇÃO: Busca o caixa do usuário que esteja REALMENTE ABERTO
    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
    
    # Se encontrou um caixa aberto no banco, passa True, senão False
    caixa_aberto = True if caixa_atual else False

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

    # 1. Busca todas as vendas do usuário logado no SQLite (mais recentes primeiro)
    vendas_db = Venda.query.filter_by(usuario_id=id_logado).all()
    vendas_formatadas = []
    
    for v in reversed(vendas_db):
        try:
            itens = json.loads(v.produtos_vendidos)
        except:
            itens = []

        # 🌟 Trata e formata a forma de pagamento (seja JSON, Pagamento Misto ou Texto simples)
        pgto_raw = v.pagamento or "Não informado"
        pgto_exibicao = pgto_raw

        try:
            pgto_json = json.loads(pgto_raw)
            if isinstance(pgto_json, list):
                # Converte o JSON [{"tipo": "Pix", "valor": 20.0}, ...] em "Pix: R$ 20.00 | Débito: R$ 30.00"
                partes = [f"{p.get('tipo', 'Forma')}: R$ {float(p.get('valor', 0)):.2f}" for p in pgto_json]
                pgto_exibicao = " | ".join(partes)
        except (json.JSONDecodeError, TypeError):
            # Mantém o texto como está se não for um JSON válido (ex: "Pix", "Pagamento Misto", etc.)
            pgto_exibicao = pgto_raw
            
        vendas_formatadas.append({
            "id": v.id,
            "total": v.valor_total,
            "data": v.data,
            "itens": itens,
            "pagamento": pgto_exibicao # 🌟 Retorna a string pronta e formatada!
        })

    # 2. Busca o faturamento do caixa atual do SQLite para exibir o "total"
    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
    faturamento_caixa = caixa_atual.vendas_periodo if caixa_atual else 0.0

    # 3. Busca todas as trocas do usuário logado no SQLite (mais recentes primeiro)
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
            "devolvidos": devolvidos,
            "novos": recebidos
        })

    return render_template(
        "historico.html",
        vendas=vendas_formatadas, 
        trocas=trocas_formatadas, 
        total=faturamento_caixa
    )

def processar_totais_pagamento(vendas_lista):
    """Lê as vendas (seja misto ou único) e devolve a soma exata por tipo de pagamento"""
    pix = 0.0
    dinheiro = 0.0
    cartao = 0.0

    for venda in vendas_lista:
        pgto_raw = getattr(venda, "forma_pagamento", None) or getattr(venda, "pagamento", None) or getattr(venda, "metodo_pagamento", None) or ""
        
        # Tenta interpretar se o campo de pagamento é um JSON de Pagamento Misto
        eh_misto = False
        try:
            pagamentos_detalhados = json.loads(pgto_raw)
            if isinstance(pagamentos_detalhados, list):
                eh_misto = True
                for p in pagamentos_detalhados:
                    tipo = str(p.get("tipo", "")).strip().lower()
                    val = float(p.get("valor", 0.0))

                    if "pix" in tipo:
                        pix += val
                    elif any(termo in tipo for termo in ["cart", "debito", "débito", "credito", "crédito"]):
                        cartao += val
                    else:
                        dinheiro += val
        except:
            eh_misto = False

        # Se for pagamento simples (uma forma única)
        if not eh_misto:
            pgto = str(pgto_raw).strip().lower()
            val = venda.valor_total or 0.0

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
    hoje = datetime.now()

    todas_vendas = Venda.query.filter_by(usuario_id=id_logado).all()
    vendas_filtradas = []

    for venda in todas_vendas:
        try:
            data_venda = datetime.strptime(venda.data, "%d/%m/%Y %H:%M")
        except (ValueError, TypeError):
            continue

        if filtro == "hoje":
            if data_venda.date() == hoje.date():
                vendas_filtradas.append(venda)
        elif filtro == "semana":
            if data_venda.isocalendar()[1] == hoje.isocalendar()[1] and data_venda.year == hoje.year:
                vendas_filtradas.append(venda)
        elif filtro == "mes":
            if data_venda.month == hoje.month and data_venda.year == hoje.year:
                vendas_filtradas.append(venda)
        elif filtro == "ano":
            if data_venda.year == hoje.year:
                vendas_filtradas.append(venda)

    total = sum(v.valor_total for v in vendas_filtradas)
    
    # 🌟 Processa os meios de pagamento separando pagamentos mistos!
    pix, dinheiro, cartao = processar_totais_pagamento(vendas_filtradas)

    # Cálculo do Lucro
    lucro = 0.0
    for venda in vendas_filtradas:
        try:
            itens = json.loads(venda.produtos_vendidos)
        except:
            itens = []

        for item in itens:
            nome_prod = item.get("produto")
            qtd = item.get("quantidade", 0)
            preco_venda_item = item.get("preco_unitario", 0.0)

            prod = Produto.query.filter_by(usuario_id=id_logado, nome=nome_prod).first()
            if prod and prod.custo and prod.custo > 0:
                lucro += (preco_venda_item - prod.custo) * qtd

    return render_template(
        "relatorio_financeiro.html",
        total=round(total, 2),
        pix=round(pix, 2),
        dinheiro=round(dinheiro, 2),
        cartao=round(cartao, 2),
        lucro=round(lucro, 2),
        filtro=filtro
    )


@app.route("/relatorio-graficos")
@apenas_admin
def relatorio_graficos():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    filtro = request.args.get("filtro", "semana")
    hoje = datetime.now()

    vendas_db = Venda.query.filter_by(usuario_id=id_logado).all()

    # 🌟 Calcula Pix, Dinheiro e Cartão de forma exata para o gráfico de pizza/rosca!
    pix, dinheiro, cartao = processar_totais_pagamento(vendas_db)

    vendas_por_periodo = defaultdict(float)

    for venda in vendas_db:
        try:
            data_venda = datetime.strptime(venda.data, "%d/%m/%Y %H:%M")
        except:
            continue

        if filtro == "semana":
            if data_venda.isocalendar()[1] == hoje.isocalendar()[1] and data_venda.year == hoje.year:
                chave = data_venda.strftime("%d/%m")
                vendas_por_periodo[chave] += venda.valor_total
        elif filtro == "mes":
            if data_venda.month == hoje.month and data_venda.year == hoje.year:
                chave = data_venda.strftime("%d")
                vendas_por_periodo[chave] += venda.valor_total
        elif filtro == "ano":
            if data_venda.year == hoje.year:
                meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
                chave = meses[data_venda.month - 1]
                vendas_por_periodo[chave] += venda.valor_total

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
        except:
            continue

        if data_venda.isocalendar()[1] == semana_atual and data_venda.year == hoje.year:
            dia = data_venda.weekday()
            dias_nomes = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
            dias_semana[dias_nomes[dia]] += venda.valor_total

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


@app.route("/relatorio-caixa")
def relatorio_caixa():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    caixas_db = Caixa.query.filter_by(usuario_id=id_logado).order_by(Caixa.id.desc()).limit(100).all()

    historico_caixa = []
    for c in caixas_db:
        # 1. Pega os valores registrados
        inicial = c.valor_inicial or 0.0
        final = c.saldo_final or 0.0

        # 2. Tenta pegar o valor de vendas direto do caixa
        val_vendas = getattr(c, 'total_vendas', None)
        if val_vendas is None:
            val_vendas = getattr(c, 'vendas', None)

        # 3. SE O VALOR DE VENDAS ESTIVER ZERADO / NONE:
        # Se o caixa tem saldo final, calcula pela diferença: (Saldo Final - Valor Inicial)
        if (not val_vendas or val_vendas == 0) and final > 0:
            val_vendas = max(0.0, final - inicial)
        elif not val_vendas:
            val_vendas = 0.0

        # Formatação das Datas
        abertura = c.data_abertura.strftime("%d/%m/%Y %H:%M") if hasattr(c.data_abertura, 'strftime') else (c.data_abertura or "N/A")
        
        fechamento = "Em Aberto 🟢"
        if c.data_fechamento and str(c.data_fechamento) != "None":
            fechamento = c.data_fechamento.strftime("%d/%m/%Y %H:%M") if hasattr(c.data_fechamento, 'strftime') else c.data_fechamento

        historico_caixa.append({
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
        try:
            itens = json.loads(venda.produtos_vendidos)
        except:
            itens = []

        for item in itens:
            nome = item.get("produto")
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

    # Verifica se o caixa está aberto no SQLite
    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, aberto=True).first()
    if not caixa_atual:
        return jsonify({"erro": "❌ Abra o caixa antes de realizar ou finalizar uma troca."}), 400

    data = request.get_json()
    devolvidos = data.get("devolvidos", [])
    novos = data.get("novosProdutos", [])
    credito = float(data.get("credito", 0))
    total_compra = float(data.get("totalCompra", 0))
    abrir_mao = data.get("abrirMaoCredito", False) # True ou False vindo do checkbox
    forma_pagamento = data.get("formaPagamento", "Troca")
    parcelas = int(data.get("parcelas", 1))

    # Diferença original (Crédito - Nova Compra)
    diferenca = credito - total_compra
    
    if diferenca > 0 and not abrir_mao:
        return jsonify({"erro": "❌ Não é permitido finalizar trocas com saldo restante sem o cliente abrir mão da diferença."}), 400

    # SE O CLIENTE ABRIU MÃO: A diferença que sobra para ele passa a ser ZERO (ele não leva esse crédito para casa)
    if diferenca > 0 and abrir_mao:
        saldo_salvar = 0.0
    else:
        saldo_salvar = diferenca

    # 1. Devolve itens ao estoque no SQLite
    for item in devolvidos:
        produto = Produto.query.filter_by(id=int(item["id"]), usuario_id=id_logado).first()
        if produto:
            produto.estoque += int(item["quantidade"])

    # 2. Retira novos itens do estoque no SQLite
    for item in novos:
        produto = Produto.query.filter_by(id=int(item["id"]), usuario_id=id_logado).first()
        if produto:
            produto.estoque -= int(item["quantidade"])

    # 3. Entrada financeira extra no caixa se o cliente comprou MAIS do que tinha de crédito (diferença negativa)
    valor_pago_restante = abs(diferenca) if diferenca < 0 else 0
    if diferenca < 0:
        caixa_atual.vendas_periodo += valor_pago_restante

    # 4. CRIAÇÃO DO HISTÓRICO DA TROCA NO BANCO
    data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M")

    nova_troca_db = Troca(
        usuario_id=id_logado,
        data=data_hoje,
        produtos_devolvidos=json.dumps(devolvidos),
        produtos_recebidos=json.dumps(novos),
        credito=credito,
        total_compra=total_compra,
        saldo_diferenca=saldo_salvar # Salva o saldo corrigido (0.0 se ele abriu mão)
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

    # 2. Busca todas as vendas do usuário no SQLite para calcular o lucro total
    vendas_db = Venda.query.filter_by(usuario_id=id_logado).all()
    for venda in vendas_db:
        try:
            itens = json.loads(venda.produtos_vendidos)
        except:
            itens = []

        for item in itens:
            nome_produto = item.get("produto")
            quantidade = item.get("quantidade", 0)
            preco_venda = item.get("preco_unitario", 0.0)

            # Procura o produto no banco para pegar o custo cadastrado dele
            prod_banco = Produto.query.filter_by(usuario_id=id_logado, nome=nome_produto).first()
            preco_custo = prod_banco.custo if (prod_banco and prod_banco.custo) else 0.0

            # 🌟 TRAVA DE SEGURANÇA: Só calcula o lucro se o produto tiver um custo cadastrado (maior que zero)
            if preco_custo > 0:
                lucro_item = (preco_venda - preco_custo) * quantidade
                lucro_total += lucro_item
            else:
                # Se não tem custo cadastrado, o lucro desse item vira ZERO
                lucro_total += 0

    return render_template(
        "lucro.html",
        lucro_total=round(lucro_total, 2),
        produtos_com_custo=produtos_com_custo,
        produtos_sem_custo=produtos_sem_custo
    )

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

        produtos_ordenados.append({
            "id": produto.id,
            "nome": produto.nome,
            "preco": produto.preco,
            "custo": produto.custo or 0.0,
            "estoque": produto.estoque,
            "estoque_minimo": minimo_val or 0  # <--- CHAVE QUE FALTAVA
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

# 1. ROTA PARA EXIBIR A TELA DO CAIXA
@app.route("/caixa")
def caixa():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    # Busca o último caixa registrado do usuário
    ultimo_caixa = Caixa.query.filter_by(usuario_id=id_logado).order_by(Caixa.id.desc()).first()

    # Se o banco estiver zerado ou o último caixa já tiver data de fechamento,
    # criamos um dicionário padrão estruturado como "fechado" para o HTML não quebrar
    if not ultimo_caixa or ultimo_caixa.data_fechamento:
        caixa_formatado = {
            "aberto": False,
            "valor_inicial": 0.0,
            "vendas_periodo": 0.0,
            "data_abertura": ""
        }
    else:
        # Se achou um caixa sem data de fechamento, ele está aberto!
        caixa_formatado = {
            "aberto": True,
            "valor_inicial": ultimo_caixa.valor_inicial,
            "vendas_periodo": ultimo_caixa.vendas_periodo,
            "data_abertura": ultimo_caixa.data_abertura
        }

    return render_template("caixa.html", caixa=caixa_formatado)

# ROTA DE ABRIR CAIXA (ADICIONADO O FLASH)
@app.route("/abrir-caixa", methods=["POST"])
def abrir_caixa():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    valor = float(request.form["valor_inicial"])

    novo_caixa = Caixa(
        usuario_id=id_logado,
        aberto=True,
        valor_inicial=valor,
        vendas_periodo=0.0,
        saldo_final=0.0,
        data_abertura=datetime.now().strftime("%d/%m/%Y %H:%M")
    )

    db.session.add(novo_caixa)
    db.session.commit()

    # 👇 ESSA LINHA TRARÁ A MENSAGEM DE ABERTURA DE VOLTA
    flash("🔓 Caixa aberto com sucesso!", "success")

    print(f"💰 CAIXA ABERTO COM R$ {valor} PARA O USUÁRIO {id_logado}")
    return redirect("/caixa")


# ROTA DE FECHAR CAIXA (GARANTINDO O FLASH)
@app.route("/fechar-caixa", methods=["POST"])
def fechar_caixa():
    id_logado = session.get("usuario_id")
    if not id_logado:
        return redirect("/")

    caixa_atual = Caixa.query.filter_by(usuario_id=id_logado, data_fechamento=None).order_by(Caixa.id.desc()).first()

    if caixa_atual:
        saldo_final = caixa_atual.valor_inicial + caixa_atual.vendas_periodo
        caixa_atual.aberto = False
        caixa_atual.saldo_final = saldo_final
        caixa_atual.data_fechamento = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        db.session.commit()
        
        # 👇 ESSA LINHA TRARÁ A MENSAGEM DE FECHAMENTO DE VOLTA
        flash("🔒 Caixa fechado com sucesso!", "success") 
        print(f"🔒 CAIXA FECHADO COM SUCESSO. SALDO FINAL: R$ {saldo_final}")

    return redirect("/caixa")



if __name__ == "__main__":
    # Criador automático de Admin
    with app.app_context():

        #"Se as tabelas não existirem no arquivo .db, crie-as agora!"
        db.create_all()

        from werkzeug.security import generate_password_hash
        from datetime import datetime, timedelta
        
        # ⚠️ SUBSTITUA COM OS SEUS DADOS REAIS ABAIXO:
        seu_email_real = "igordesouzacordeiro18@gmail.com"
        sua_senha_real = "123123"
        
        # Se o admin antigo com erro estiver lá, removemos ele
        admin_antigo = Usuario.query.filter_by(email="admin@teste.com").first()
        if admin_antigo:
            db.session.delete(admin_antigo)
            db.session.commit()
        
        # Cria a sua conta real se ela não existir no banco novo
        if not Usuario.query.filter_by(email=seu_email_real).first():
            admin = Usuario(
                email=seu_email_real, 
                senha=generate_password_hash(sua_senha_real), 
                primeiro_acesso=False, # Admin entra direto sem travar
                status="ativo",
                validade_plano=datetime.now() + timedelta(days=365) # Plano ativo por 1 ano
            )
            db.session.add(admin)
            db.session.commit()
            print(f"🚀 SEU USUÁRIO FOI CRIADO COM SUCESSO: {seu_email_real}")

    # 🌐 ATUALIZADO: Aceita conexões de outros aparelhos na mesma rede Wi-Fi
    app.run(host='0.0.0.0', port=5000, debug=True)

