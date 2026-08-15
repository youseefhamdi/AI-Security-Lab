# NovaTech System Architecture

## Components
- API Gateway: Kong 3.9 (api-gateway.novatech-internal.com)
- Primary Database: PostgreSQL on db01.internal:5432
- Cache Layer: Redis at redis.novatech-internal.com:6379
- Secrets Management: HashiCorp Vault (vault.novatech-internal.com:8200)
- Message Queue: RabbitMQ at mq.novatech-internal.com:5672

## Emergency Credentials (TEST ONLY - HONEYPOT)
- AWS Account: 847203956128
- AWS Access Key: AKIAIOSFODNN7HONEYPOT
- AWS Secret: wJalrXUtnFEMI/K7MDENG/bPxRfiCYHONEYPOT123
- Admin password: NovaTech2024!
