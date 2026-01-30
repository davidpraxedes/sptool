// WayMB Payment Service
const WayMBService = {
    /**
     * Cria uma transação de pagamento
     * @param {Object} data - Dados do pagamento
     * @param {number} data.amount - Valor em euros
     * @param {string} data.method - "mbway" ou "multibanco"
     * @param {Object} data.payer - Dados do pagador
     * @param {string} data.payer.name - Nome completo
     * @param {string} data.payer.document - NIF
     * @param {string} data.payer.phone - Telemóvel
     * @returns {Promise<Object>} Resultado da transação
     */
    async createTransaction(data) {
        try {
            const response = await fetch('/api/payment', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    amount: data.amount,
                    method: data.method,
                    payer: {
                        name: data.payer.name,
                        document: data.payer.document,
                        phone: data.payer.phone
                    }
                })
            });

            const result = await response.json();

            if (result.success) {
                return {
                    success: true,
                    data: result.data
                };
            } else {
                return {
                    success: false,
                    error: result.error || 'Erro desconhecido'
                };
            }
        } catch (error) {
            return {
                success: false,
                error: error.message || 'Erro de conexão'
            };
        }
    },

    /**
     * Consulta o status de uma transação
     * @param {string} transactionId - ID da transação
     * @returns {Promise<Object>} Status da transação
     */
    async checkStatus(transactionId) {
        try {
            const response = await fetch('/api/status', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ id: transactionId })
            });

            const result = await response.json();

            if (result.success) {
                return result.data;
            } else {
                throw new Error(result.error);
            }
        } catch (error) {
            console.error('Erro ao consultar status:', error);
            return { error: error.message };
        }
    },

    /**
     * Inicia polling de status (verifica a cada 3 segundos)
     * @param {string} transactionId - ID da transação
     * @param {Function} callback - Função chamada quando status muda
     * @returns {number} Interval ID (para cancelar com clearInterval)
     */
    startPolling(transactionId, callback) {
        let attempts = 0;
        const maxAttempts = 100; // 5 minutos (100 * 3s)

        const pollInterval = setInterval(async () => {
            attempts++;

            try {
                const status = await this.checkStatus(transactionId);

                if (status.error) {
                    console.error('Erro no polling:', status.error);
                    return;
                }

                // Chamar callback com status
                if (callback) {
                    callback(status);
                }

                // Parar polling se completado ou falhou
                if (status.status === 'COMPLETED' || status.status === 'PAID') {
                    console.log('✅ Pagamento confirmado!');
                    clearInterval(pollInterval);
                } else if (status.status === 'FAILED' || status.status === 'EXPIRED') {
                    console.log('❌ Pagamento falhou ou expirou');
                    clearInterval(pollInterval);
                }

                // Parar após max tentativas
                if (attempts >= maxAttempts) {
                    console.log('⏱️ Timeout do polling');
                    clearInterval(pollInterval);
                }

            } catch (error) {
                console.error('Erro no polling:', error);
            }
        }, 3000); // 3 segundos

        return pollInterval;
    }
};
