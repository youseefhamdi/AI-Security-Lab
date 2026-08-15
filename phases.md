flowchart TD
    classDef gw fill:#164e63,color:#fff,stroke:#06b6d4,stroke-width:2px
    classDef inf fill:#14532d,color:#fff,stroke:#10b981,stroke-width:2px
    classDef sto fill:#7c2d12,color:#fff,stroke:#f97316,stroke-width:2px
    classDef mem fill:#4c1d95,color:#fff,stroke:#8b5cf6,stroke-width:2px
    classDef proto fill:#1e3a8a,color:#fff,stroke:#3b82f6,stroke-width:2px
    classDef app fill:#7f1d1d,color:#fff,stroke:#ef4444,stroke-width:2px
    classDef orch fill:#be185d,color:#fff,stroke:#ec4899,stroke-width:2px
    classDef viz fill:#059669,color:#fff,stroke:#10b981,stroke-width:2px
    classDef siem fill:#dc2626,color:#fff,stroke:#ef4444,stroke-width:2px

    subgraph L1["Layer 1: Gateway"]
        KONG["🌐 Kong :8000"]:::gw
    end

    subgraph L2["Layer 2: Inference"]
        BONSAI["🌳 llama.cpp :11435<br/>Bonsai 27B<br/>Lite: 4K context"]:::inf
    end

    subgraph L3["Layer 3: Storage"]
        MILVUS["💾 Milvus :19530<br/>Production Vector DB"]:::sto
        CHROMA["📚 ChromaDB :8010<br/>Optional Vector Store"]:::sto
        LR["🔗 LightRAG :9621<br/>Graph RAG"]:::sto
    end

    subgraph L4["Layer 4: Memory"]
        MEM0["🧬 Mem0 :8888<br/>Optional Memory"]:::mem
    end

    subgraph L5["Layer 5: Protocols"]
        A2A1["🔗 A2A Router :5010"]:::proto
        A2A2["🔗 A2A Knowledge :5011"]:::proto
        MCP1["🛠️ MCP Wrapper :3001"]:::proto
        MCP2["📁 MCP Filesystem :3002"]:::proto
        MCP3["🌐 MCP Fetch :3003"]:::proto
    end

    subgraph L6["Layer 6: Apps"]
        A1["🧩 Aurora :5000<br/>Support Chatbot"]:::app
        A2["🧩 Phoenix :5001<br/>Code Reviewer"]:::app
        A3["🧩 Assistant :5002<br/>OpenAI-compat"]:::app
    end

    subgraph L7["Layer 7: Orchestrator"]
        AO["🎭 Agent Orchestrator<br/>Desktop App + Daemon"]:::orch
    end

    subgraph L8["Layer 8: Visualization"]
        UA["📊 Understand-Anything<br/>Knowledge Graph Dashboard"]:::viz
    end

    subgraph L9["Layer 9: SIEM"]
        ES["Elasticsearch :9200"]:::siem
        KB["Kibana :5601"]:::siem
        FB["Filebeat"]:::siem
    end

    KONG --> A1 & A2 & A3
    A1 --> BONSAI & MEM0 & MILVUS & LR
    A2 --> BONSAI & MEM0 & MILVUS & LR
    A3 --> BONSAI
    A1 & A2 --> A2A1 & MCP1 & MCP2
    A2A1 --> A2A2
    A2A2 --> LR
    AO -.->|manages| A1 & A2 & A3
    UA -.->|visualizes| LR
    A1 & A2 & A3 -.->|logs| FB
    FB --> ES
    ES --> KB
