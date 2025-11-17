# 🍲 Tempero das Gurias — Fechamento Financeiro

Aplicação em **Python + Streamlit** para automatizar o fechamento financeiro da Tempero das Gurias usando extratos do **Itaú** e **PagSeguro**.

O sistema calcula entradas/saídas, classifica categorias automaticamente e gera relatórios em Excel, além de manter um histórico de fechamentos.

---

## 🚀 Funcionalidades

- Upload de extratos Itaú/PagSeguro (.csv ou .xlsx)
- Cálculo automático:
  - Entradas / Saídas
  - Resultado consolidado
  - Saldo final
- Classificação automática por categoria
- Geração de relatório Excel:
  - Resumo
  - Categorias
  - Movimentos
- Histórico de fechamentos salvos

---

## ▶️ Executar Localmente

```bash
pip install -r requirements.txt
streamlit run fechamento_tempero_app.py

Acesse em:
http://localhost:8501

☁️ Deploy no Streamlit Cloud (gratuito)

Suba este projeto para o GitHub

Acesse: https://share.streamlit.io

Clique em New App

Selecione:

Repositório: Tempero-Fechamento

Arquivo: fechamento_tempero_app.py

Deploy 🎉
URL ficará assim:
https://<nome>.streamlit.app

📄 Licença

Uso interno da Tempero das Gurias.

