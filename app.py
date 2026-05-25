import json
from datetime import datetime
from flask import Flask, render_template, request, redirect

app = Flask(__name__)


@app.route("/")
def login():
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():

    total_vendas = len(dados["vendas"])

    total_produtos = len(dados["produtos"])

    valor_total = sum(
        venda["total"]
        for venda in dados["vendas"]
    )

    ultimas_vendas = list(
        reversed(dados["vendas"])
    )[:5]

    contador = {}

    for venda in dados["vendas"]:
        for item in venda["itens"]:
            nome = item["produto"]

            quantidade = item["quantidade"]

            if nome in contador:

                contador[nome] += quantidade

            else:

                contador[nome] = quantidade

            if nome in contador:

                contador[nome] += quantidade

            else:

                contador[nome] = quantidade

    produto_mais_vendido = "Nenhum"

    if contador:

        produto_mais_vendido = max(
            contador,
            key=contador.get
        )

    pix = sum(
        venda["total"]
        for venda in dados["vendas"]
        if venda["pagamento"] == "Pix"
    )

    cartao = sum(
        venda["total"]
        for venda in dados["vendas"]
        if (
            venda["pagamento"] == "Débito"
            or
            "Crédito" in venda["pagamento"]
        )
    )

    dinheiro = sum(
        venda["total"]
        for venda in dados["vendas"]
        if venda["pagamento"] == "Dinheiro"
    )

    return render_template(
        "dashboard.html",
        total_vendas=total_vendas,
        total_produtos=total_produtos,
        valor_total=valor_total,
        ultimas_vendas=ultimas_vendas,
        produto_mais_vendido=produto_mais_vendido,
        pix=pix,
        cartao=cartao,
        dinheiro=dinheiro
    )

@app.route("/produtos")
def produtos():

    produtos_ordenados = sorted(
        dados["produtos"],
        key=lambda p: p["nome"].lower()
    )

    return render_template(
        "produtos.html",
        produtos=produtos_ordenados
    )

@app.route("/salvar-venda", methods=["POST"])
def salvar_venda():

    carrinho_json = request.form.get(
        "carrinho"
    )

    if not carrinho_json:

        return "❌ Carrinho vazio"

    carrinho = json.loads(
        carrinho_json
    )

    pagamento = request.form["pagamento"]

    total_geral = 0

    itens = []

    for item in carrinho:

        produto_encontrado = None

        for produto in dados["produtos"]:

            nome_produto = (
                item["nome"]
                .split(" - R$")[0]
                .strip()
            )

            if produto["nome"] == nome_produto:

                produto_encontrado = produto
                break

        if produto_encontrado is None:

            return f"""
            ❌ Produto não encontrado:
            {item['nome']}
            """

        # Só verifica estoque se ele for maior que 0
        if produto_encontrado["estoque"] > 0:

            if (
                item["quantidade"]
                >
                produto_encontrado["estoque"]
            ):

                return f"""
                ❌ Estoque insuficiente para:
                {item['nome']}

                Estoque atual:
                {produto_encontrado['estoque']}
                """

        subtotal = (
            produto_encontrado["preco"]
            * item["quantidade"]
        )

        lucro_item = (
            (
                produto_encontrado["preco"]
                -
                produto_encontrado["custo"]
            )
            *
            item["quantidade"]
        )

        # Só desconta estoque se ele for maior que 0
        if produto_encontrado["estoque"] > 0:

            produto_encontrado["estoque"] -= (
                item["quantidade"]
            )

        total_geral += subtotal

        itens.append({

            "produto":
            produto_encontrado["nome"],

            "quantidade":
            item["quantidade"],

            "subtotal":
            subtotal,

            "lucro":
            lucro_item

        })

    venda = {

        "id":
        len(dados["vendas"]) + 1,

        "itens":
        itens,

        "total":
        total_geral,

        "pagamento":
        pagamento,

        "data":
        datetime.now().strftime(
            "%d/%m/%Y %H:%M"
        )
    }

    dados["vendas"].append(venda)

    salvar_dados(dados)

    return redirect("/historico")


@app.route("/salvar-produto", methods=["POST"])
def salvar_produto():

    global dados

    dados = carregar_dados()

    nome = request.form["nome"]

    preco = float(
        request.form["preco"]
    )

    novo_id = 1

    if dados["produtos"]:

        novo_id = max(
            produto["id"]
            for produto in dados["produtos"]
        ) + 1

    produto = {

        "id":
        novo_id,

        "nome":
        nome,

        "preco":
        preco,

        "estoque":
        0,

        "estoque_minimo":
        0,

        "custo":
        0
    }

    dados["produtos"].append(produto)

    salvar_dados(dados)

    return redirect("/produtos")


@app.route("/novo-produto")
def novo_produto():
    return render_template("novo_produto.html")


@app.route("/excluir-venda/<int:id>")
def excluir_venda(id):

    venda_encontrada = None

    for venda in dados["vendas"]:

        if venda["id"] == id:

            venda_encontrada = venda
            break

    if venda_encontrada is None:

        return redirect("/historico")

    for item in venda_encontrada["itens"]:

        for produto in dados["produtos"]:

            if produto["nome"] == item["produto"]:

                produto["estoque"] += item["quantidade"]

                break

    dados["vendas"].remove(venda_encontrada)

    salvar_dados(dados)

    return redirect("/historico")

@app.route("/excluir-produto/<int:id>")
def excluir_produto(id):

    produto_encontrado = None

    for produto in dados["produtos"]:

        if produto["id"] == id:

            produto_encontrado = produto
            break

    if produto_encontrado is not None:

        dados["produtos"].remove(produto_encontrado)

        salvar_dados(dados)

    return redirect("/produtos")


@app.route("/editar-produto/<int:id>")
def editar_produto(id):

    produto_encontrado = None

    for produto in dados["produtos"]:

        if produto["id"] == id:

            produto_encontrado = produto

            break

    return render_template(
        "editar_produto.html",
        produto=produto_encontrado
    )

@app.route("/atualizar-produto/<int:id>", methods=["POST"])
def atualizar_produto(id):

    nome = request.form["nome"]

    preco = float(request.form["preco"])

    for produto in dados["produtos"]:

        if produto["id"] == id:

            produto["nome"] = nome
            produto["preco"] = preco

            break

    salvar_dados(dados)

    return redirect("/produtos")

@app.route("/nova-venda")
def nova_venda():

    produtos_ordenados = sorted(
        dados["produtos"],
        key=lambda p: p["nome"].lower()
    )

    return render_template(
        "nova_venda.html",
        produtos=produtos_ordenados
    )

@app.route("/historico")
def historico():

    vendas_ordenadas = sorted(
        dados["vendas"],
        key=lambda v: v["data"],
        reverse=True
    )

    total_vendido = sum(
        venda["total"]
        for venda in dados["vendas"]
    )

    return render_template(
        "historico.html",
        vendas=vendas_ordenadas,
        total=total_vendido
    )


@app.route("/relatorios")
def relatorios():

    filtro = request.args.get("filtro", "hoje")

    hoje = datetime.now()

    vendas_filtradas = []

    for venda in dados["vendas"]:

        data_venda = datetime.strptime(
            venda["data"],
            "%d/%m/%Y %H:%M"
        )

        if filtro == "hoje":

            if data_venda.date() == hoje.date():
                vendas_filtradas.append(venda)

        elif filtro == "semana":

            if (
                data_venda.isocalendar()[1]
                ==
                hoje.isocalendar()[1]
            ):
                vendas_filtradas.append(venda)

        elif filtro == "mes":

            if (
                data_venda.month == hoje.month
                and
                data_venda.year == hoje.year
            ):
                vendas_filtradas.append(venda)

        elif filtro == "ano":

            if data_venda.year == hoje.year:
                vendas_filtradas.append(venda)

    total = sum(
        venda["total"]
        for venda in vendas_filtradas
    )

    pix = sum(
        venda["total"]
        for venda in vendas_filtradas
        if venda["pagamento"] == "Pix"
    )

    dinheiro = sum(
        venda["total"]
        for venda in vendas_filtradas
        if venda["pagamento"] == "Dinheiro"
    )

    cartao = sum(
        venda["total"]
        for venda in vendas_filtradas
        if (
            venda["pagamento"] == "Débito"
            or
            "Crédito" in venda["pagamento"]
        )
    )

    lucro = 0

    for venda in vendas_filtradas:

        for item in venda["itens"]:

            lucro += item.get("lucro", 0)

    return render_template(
        "relatorios.html",
        total=total,
        pix=pix,
        dinheiro=dinheiro,
        cartao=cartao,
        lucro=lucro
    )



@app.route("/troca/<int:id>")
def troca(id):

    venda = None

    for v in dados["vendas"]:

        if v["id"] == id:

            venda = v
            break

    if venda is None:
        return redirect("/historico")

    produtos_ordenados = sorted(
        dados["produtos"],
        key=lambda p: p["nome"].lower()
    )

    return render_template(
        "troca.html",
        venda=venda,
        produtos=produtos_ordenados
    )

@app.route("/trocar-item/<int:id_venda>/<int:indice_item>")
def trocar_item(id_venda, indice_item):

    venda_encontrada = None

    for venda in dados["vendas"]:

        if venda["id"] == id_venda:

            venda_encontrada = venda
            break

    if venda_encontrada is None:

        return redirect("/historico")

    item = venda_encontrada["itens"][indice_item]

    produtos_ordenados = sorted(
        dados["produtos"],
        key=lambda p: p["nome"].lower()
    )

    return render_template(
        "trocar_item.html",
        venda=venda_encontrada,
        item=item,
        indice_item=indice_item,
        produtos=produtos_ordenados
    )


@app.route(
    "/confirmar-troca-item/<int:id_venda>/<int:indice_item>",
    methods=["POST"]
)
def confirmar_troca_item(id_venda, indice_item):

    venda = None

    for v in dados["vendas"]:

        if v["id"] == id_venda:

            venda = v
            break

    if venda is None:

        return redirect("/historico")

    item_antigo = venda["itens"][indice_item]

    id_produto = int(
        request.form["produto"]
    )

    quantidade = int(
        request.form["quantidade"]
    )

    produto_novo = None

    for produto in dados["produtos"]:

        if produto["id"] == id_produto:

            produto_novo = produto
            break

    if produto_novo is None:

        return redirect("/historico")

    valor_antigo = item_antigo["subtotal"]

    valor_novo = (
        produto_novo["preco"]
        * quantidade
    )

    diferenca = valor_novo - valor_antigo

    novo_subtotal = max(
        valor_antigo,
        valor_novo
    )

    saldo_restante = 0

    if valor_novo < valor_antigo:

        saldo_restante = (
            valor_antigo
            - valor_novo
        )

    for produto in dados["produtos"]:

        if produto["nome"] == item_antigo["produto"]:

            produto["estoque"] += item_antigo["quantidade"]

            break

    if quantidade > produto_novo["estoque"]:

        return f"""
        ❌ Estoque insuficiente para:
        {produto_novo['nome']}

        Estoque atual:
        {produto_novo['estoque']}
        """

    produto_novo["estoque"] -= quantidade

    item_antigo["produto"] = (
        produto_novo["nome"]
    )

    item_antigo["quantidade"] = quantidade

    item_antigo["subtotal"] = novo_subtotal

    novo_total = 0

    for item in venda["itens"]:

        novo_total += item["subtotal"]

    venda["total"] = novo_total

    venda["troca"] = True

    venda["saldo_restante"] = saldo_restante

    venda["data_troca"] = (
        datetime.now().strftime("%d/%m/%Y %H:%M")
    )

    salvar_dados(dados)

    return render_template(
        "troca_sucesso.html",
        diferenca=diferenca,
        saldo_restante=saldo_restante
    )

@app.route("/lucro")
def lucro():

    lucro_total = 0

    produtos_com_custo = []

    produtos_sem_custo = []

    for produto in dados["produtos"]:

        if produto["custo"] > 0:

            produtos_com_custo.append(produto)

        else:

            produtos_sem_custo.append(produto)

    for venda in dados["vendas"]:

        for item in venda["itens"]:

            lucro_item = item.get("lucro")

            if lucro_item is not None:

                lucro_total += lucro_item

    return render_template(
        "lucro.html",
        lucro_total=lucro_total,
        produtos_com_custo=produtos_com_custo,
        produtos_sem_custo=produtos_sem_custo
    )

@app.route("/gestao")
def gestao():

    produtos_ordenados = sorted(
        dados["produtos"],
        key=lambda p: p["nome"].lower()
    )

    return render_template(
        "gestao.html",
        produtos=produtos_ordenados
    )


@app.route("/estoque")
def estoque():

    dados = carregar_dados()

    produtos = dados["produtos"]

    return render_template(
        "estoque.html",
        produtos=produtos
    )


@app.route("/criar-estoque")
def criar_estoque():

    dados = carregar_dados()

    produtos = dados["produtos"]

    return render_template(
        "criar_estoque.html",
        produtos=produtos
    )

@app.route("/salvar-estoque", methods=["POST"])
def salvar_estoque():

    global dados

    dados = carregar_dados()

    produto_id = int(
        request.form["produto"]
    )

    estoque = int(
        request.form["estoque"]
    )

    estoque_minimo = int(
        request.form["estoque_minimo"]
    )

    for produto in dados["produtos"]:

        if produto["id"] == produto_id:

            produto["estoque"] = estoque

            produto["estoque_minimo"] = estoque_minimo

            break

    salvar_dados(dados)

    return redirect("/estoque")

@app.route("/editar-gestao/<int:id>")
def editar_gestao(id):

    produto_encontrado = None

    for produto in dados["produtos"]:

        if produto["id"] == id:

            produto_encontrado = produto

            break

    return render_template(
        "editar_gestao.html",
        produto=produto_encontrado
    )


@app.route(
    "/salvar-gestao/<int:id>",
    methods=["POST"]
)
def salvar_gestao(id):

    global dados

    dados = carregar_dados()

    for produto in dados["produtos"]:

        if produto["id"] == id:

            produto["preco"] = float(
                request.form["preco"]
            )

            produto["custo"] = float(
                request.form["custo"]
            )

            produto["estoque"] = int(
                request.form["estoque"]
            )

            produto["estoque_minimo"] = int(
                request.form["estoque_minimo"]
            )

            break

    salvar_dados(dados)

    return redirect("/gestao")

ARQUIVO = "dados.json"


def carregar_dados():
    try:
        with open(ARQUIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"produtos": [], "vendas": []}


def salvar_dados(dados):
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=4, ensure_ascii=False)


dados = carregar_dados()

for i, venda in enumerate(dados["vendas"]):

    if "id" not in venda:

        venda["id"] = i + 1

salvar_dados(dados)


def cadastrar_produto():
    nome = input("Nome do produto: ")

    try:
        preco = float(input("Preço: "))
    except:
        print("❌ Preço inválido")
        return

    produto = {
        "nome": nome,
        "preco": preco
    }

    dados["produtos"].append(produto)
    salvar_dados(dados)

    print("✅ Produto cadastrado!")


def listar_produtos():
    print("\n📦 Produtos:")

    if not dados["produtos"]:
        print("Nenhum produto cadastrado.")
        return

    for i, p in enumerate(dados["produtos"]):
        print(f"{i} - {p['nome']} | R${p['preco']:.2f}")


def registrar_venda():
    if not dados["produtos"]:
        print("❌ Nenhum produto cadastrado.")
        return

    listar_produtos()

    try:
        indice = int(input("Escolha o produto: "))
        quantidade = int(input("Quantidade: "))
    except:
        print("❌ Valor inválido")
        return

    if indice < 0 or indice >= len(dados["produtos"]):
        print("❌ Produto inválido")
        return

    produto = dados["produtos"][indice]

    total = produto["preco"] * quantidade

    venda = {
        "produto": produto["nome"],
        "quantidade": quantidade,
        "total": total,
        "data": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    dados["vendas"].append(venda)
    salvar_dados(dados)

    print(f"💰 Venda registrada! Total: R${total:.2f}")


def total_do_dia():
    total = sum(v["total"] for v in dados["vendas"])
    print(f"\n📊 Total geral vendido: R${total:.2f}")


def historico_vendas():
    print("\n📜 Histórico de vendas:\n")

    if not dados["vendas"]:
        print("Nenhuma venda registrada.")
        return

    for v in dados["vendas"]:
        print(
            f"{v['data']} | "
            f"{v['produto']} | "
            f"Qtd: {v['quantidade']} | "
            f"R${v['total']:.2f}"
        )


def produto_mais_vendido():
    contador = {}

    for v in dados["vendas"]:
        nome = v["produto"]
        quantidade = v["quantidade"]

        if nome in contador:
            contador[nome] += quantidade
        else:
            contador[nome] = quantidade

    if contador:
        mais_vendido = max(contador, key=contador.get)

        print(
            f"\n🏆 Produto mais vendido: "
            f"{mais_vendido} "
            f"({contador[mais_vendido]} unidades)"
        )
    else:
        print("Nenhuma venda ainda.")


def vendas_hoje():
    hoje = datetime.now().strftime("%Y-%m-%d")
    total = 0

    print("\n📆 Vendas de hoje:\n")

    encontrou = False

    for v in dados["vendas"]:
        if v["data"].startswith(hoje):
            encontrou = True

            print(
                f"{v['data']} | "
                f"{v['produto']} | "
                f"Qtd: {v['quantidade']} | "
                f"R${v['total']:.2f}"
            )

            total += v["total"]

    if not encontrou:
        print("Nenhuma venda hoje.")

    print(f"\n💰 Total de hoje: R${total:.2f}")


def vendas_mes():
    mes_atual = datetime.now().strftime("%Y-%m")
    total = 0

    print("\n📅 Vendas do mês:\n")

    encontrou = False

    for v in dados["vendas"]:
        if v["data"].startswith(mes_atual):
            encontrou = True

            print(
                f"{v['data']} | "
                f"{v['produto']} | "
                f"Qtd: {v['quantidade']} | "
                f"R${v['total']:.2f}"
            )

            total += v["total"]

    if not encontrou:
        print("Nenhuma venda este mês.")

    print(f"\n💰 Total do mês: R${total:.2f}")


def remover_produto():
    listar_produtos()

    if not dados["produtos"]:
        return

    try:
        indice = int(input("Digite o índice do produto: "))
    except:
        print("❌ Valor inválido")
        return

    if indice < 0 or indice >= len(dados["produtos"]):
        print("❌ Produto inválido")
        return

    removido = dados["produtos"].pop(indice)

    salvar_dados(dados)

    print(f"🗑️ Produto removido: {removido['nome']}")


def menu():
    while True:
        print("\n===== MENU =====")
        print("1 - Cadastrar produto")
        print("2 - Listar produtos")
        print("3 - Registrar venda")
        print("4 - Total geral")
        print("5 - Histórico de vendas")
        print("6 - Produto mais vendido")
        print("7 - Vendas de hoje")
        print("8 - Vendas do mês")
        print("9 - Remover produto")
        print("0 - Sair")

        opcao = input("Escolha: ")

        if opcao == "1":
            cadastrar_produto()

        elif opcao == "2":
            listar_produtos()

        elif opcao == "3":
            registrar_venda()

        elif opcao == "4":
            total_do_dia()

        elif opcao == "5":
            historico_vendas()

        elif opcao == "6":
            produto_mais_vendido()

        elif opcao == "7":
            vendas_hoje()

        elif opcao == "8":
            vendas_mes()

        elif opcao == "9":
            remover_produto()

        elif opcao == "0":
            print("👋 Encerrando sistema...")
            break

        else:
            print("❌ Opção inválida")

if __name__ == "__main__":
    app.run(debug=True)