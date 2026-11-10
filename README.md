# Controle de Saldos Remanescentes — Cartões de Estacionamento

Aplicação Web Fullstack (Python/Flask + SQLite) para consulta e baixa de saldos remanescentes de cartões pré-pagos de estacionamento pelas operadoras de caixa.

## Funcionalidades
- **Frente de Caixa (PDV)**: Consulta rápida por número do cartão ou nome do cliente (`F2`).
- **Baixa Total e Baixa Parcial**: Desconta automaticamente o valor da estadia; quando o saldo do cartão é inferior ao valor da estadia, zera o cartão e informa em destaque a diferença a cobrar no caixa.
- **Comprovante e Estorno**: Impressão de comprovante de atendimento e opção de estorno auditado.
- **Histórico e Exportação Excel (`.xlsx`)**: Extrato completo de movimentações por operadora/período e exportação da planilha de saldos atualizada.
- **Painel Administrativo**: Gestão de operadoras de caixa e inclusão de novos cartões.

## Como Executar
1. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
2. Inicie o sistema pelo atalho `INICIAR_SISTEMA.bat` ou via terminal:
   ```bash
   python app.py
   ```
3. Acesse `http://localhost:5000`.

