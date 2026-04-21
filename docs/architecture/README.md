# Architecture Diagrams

Visual reference for how Agent Austin fits together. All diagrams use Mermaid and render natively in GitHub.

## Contents

| Diagram | What it shows |
|---|---|
| [High-Level System](#1-high-level-system) | Browser → Next.js → FastAPI → Claude / Postgres / Volume / Socrata |
| [Chat Streaming Flow](#2-chat-streaming-flow-sse) | End-to-end request lifecycle for a chat turn |
| [AI Elements Rendering](#3-ai-elements-rendering-pipeline) | How SSE events become rendered UI |
| [Claude Agent SDK Wiring](#4-claude-agent-sdk--tool--mcp-wiring) | Allowed tools, custom MCP server, permissions |
| [Railway Deployment](#5-railway-deployment-topology) | Two services, Postgres plugin, persistent volume |
| [Component → File Map](#6-component--file-map) | Which file owns which responsibility |
| [Data Pipeline](#7-data-pipeline--311-delta-merge) | How 311 data is kept fresh on startup |
| [Auth Flow](#8-auth-flow) | JWT issuance, storage, and validation |

---

## 1. High-Level System

```mermaid
graph TB
    subgraph Browser["User Browser"]
        UI[Next.js 16 UI<br/>AI Elements + shadcn]
    end

    subgraph Railway["Railway Platform"]
        subgraph FE["Frontend Service (agentui/)"]
            Next[Next.js App Router<br/>Streamdown + Tailwind 4]
        end

        subgraph BE["Backend Service (agent311/)"]
            FastAPI[FastAPI + uvicorn]
            SDK[Claude Agent SDK<br/>ClaudeSDKClient]
            MCP[In-process MCP Server<br/>agent311_host]
        end

        PG[(PostgreSQL<br/>sessions / messages)]
        VOL[(Persistent Volume<br/>RAILWAY_VOLUME_MOUNT_PATH)]
    end

    subgraph External["External Services"]
        Anthropic[Anthropic API<br/>Claude models]
        Socrata[Socrata Open Data API<br/>xwdj-i9he]
    end

    UI -->|HTTPS + JWT| Next
    Next -->|fetch SSE<br/>NEXT_PUBLIC_API_URL| FastAPI
    FastAPI --> SDK
    SDK <--> MCP
    SDK -->|streaming| Anthropic
    FastAPI <-->|async SQLAlchemy| PG
    MCP <-->|view/save files| VOL
    FastAPI -->|start.sh<br/>delta merge| Socrata
    Socrata --> VOL
```

---

## 2. Chat Streaming Flow (SSE)

```mermaid
sequenceDiagram
    participant U as User
    participant C as chat.tsx (Next.js)
    participant F as FastAPI /api/chat
    participant S as ClaudeSDKClient
    participant A as Anthropic API
    participant M as MCP Tools
    participant DB as PostgreSQL

    U->>C: Send prompt
    C->>F: POST /api/chat<br/>Bearer JWT + messages[]
    F->>DB: Persist user message
    F->>S: client.query(prompt)
    S->>A: Stream request

    loop Agent turns (max 60)
        A-->>S: AssistantMessage blocks
        alt TextBlock
            S-->>F: text chunk
            F-->>C: data: text-delta
        else ToolUseBlock
            S->>M: view_content / save_chart / save_report
            M->>VOL: read/write file
            M-->>S: result
            F-->>C: data: text-delta<br/>"[Using tool: ...]"
        end
    end

    F->>DB: Persist assistant message
    F-->>C: data: finish<br/>data: [DONE]
    C->>C: Parse markers, fetch artifacts
    C-->>U: Render with AI Elements
```

---

## 3. AI Elements Rendering Pipeline

```mermaid
graph LR
    subgraph Stream["SSE Events"]
        E1[text-delta chunks]
        E2["[Using tool: view_content PATH]"]
    end

    subgraph Parse["chat.tsx"]
        P1[Buffer + JSON.parse]
        P2[Regex extract tool markers]
        P3[authFetch /api/fetch_file]
    end

    subgraph Elements["AI Elements Components"]
        Msg[Message / MessageContent]
        Prompt[PromptInput]
        Art[Artifact card]
        Code[CodeBlock]
        JSX[JSXPreview]
        SD[Streamdown<br/>md + mermaid + math + syntax]
    end

    subgraph Panel["Artifact Panel"]
        IF[iframe for HTML]
        JP[JSXPreview for JSX/TSX]
        IMG[img for PNG]
    end

    E1 --> P1 --> SD --> Msg
    E2 --> P2 --> P3
    P3 -->|HTML/JSX/PNG| Art
    Art --> Code
    Art --> IF
    Art --> JP
    Art --> IMG
```

---

## 4. Claude Agent SDK — Tool & MCP Wiring

```mermaid
graph TB
    subgraph Client["ClaudeSDKClient options"]
        SP[system_prompt<br/>Agent 311 instructions]
        PM[permission_mode<br/>acceptEdits]
        MT[max_turns: 60]
        SS["setting_sources: project"]
    end

    subgraph Tools["allowed_tools"]
        direction LR
        Builtin[Built-in:<br/>Read, Write, Edit,<br/>Bash, Task, Skill,<br/>WebSearch, WebFetch]
        Custom[MCP server:<br/>agent311_host]
    end

    subgraph MCPTools["Custom MCP Tools"]
        VC["view_content(path)<br/>200KB max<br/>/tmp + volume only<br/>.html .js .jsx .tsx .png .csv"]
        SC["save_chart(filename, content, encoding)<br/>→ volume/analysis/charts/"]
        SR["save_report(filename, content, encoding)<br/>→ volume/reports/"]
    end

    Client --> Tools
    Custom --> VC
    Custom --> SC
    Custom --> SR

    VC -.reads.-> VOL[(Volume)]
    SC -.writes.-> VOL
    SR -.writes.-> VOL
```

---

## 5. Railway Deployment Topology

```mermaid
graph TB
    subgraph RailwayProject["Railway Project"]
        direction TB

        subgraph FeService["Frontend Service"]
            FR[Root Dir: /agentui]
            FRP[railpack.json<br/>auto-detect Next.js]
            FEnv["NEXT_PUBLIC_API_URL"]
        end

        subgraph BeService["Backend Service"]
            BR[Root Dir: /agent311]
            BRP[railpack.json<br/>uv via mise]
            SH[start.sh<br/>1. download_311.py<br/>2. uvicorn main:app]
            BEnv["DATABASE_URL<br/>JWT_SECRET<br/>ANTHROPIC_API_KEY<br/>RAILWAY_VOLUME_MOUNT_PATH"]
        end

        subgraph PgPlugin["Postgres Plugin"]
            PG[(sessions<br/>messages)]
        end

        subgraph VolPlugin["Volume Plugin"]
            V[(311_recent.csv<br/>reports/<br/>analysis/charts/)]
        end
    end

    FeService -->|HTTPS| BeService
    BeService <--> PgPlugin
    BeService <--> VolPlugin
    BeService -->|startup| Socrata[Socrata API]
```

---

## 6. Component → File Map

```mermaid
graph LR
    subgraph Backend["agent311/agent311/"]
        M[main.py<br/>FastAPI + SSE + MCP tools]
        A[auth.py<br/>JWT + login]
        D[db.py<br/>Session/Message ORM]
        DL[download_311.py<br/>Socrata delta merge]
    end

    subgraph Frontend["agentui/"]
        CH[components/chat.tsx<br/>SSE orchestrator]
        CM[components/chat-messages.tsx<br/>Tool summary + artifacts]
        SB[components/sidebar.tsx<br/>Sessions + favorites]
        AP[components/artifact-panel.tsx<br/>iframe + JSXPreview]
        LA[lib/auth.ts<br/>JWT + authFetch]
        LS[lib/session-api.ts<br/>Session CRUD]
    end

    CH --> LA
    CH --> LS
    CH --> CM
    CM --> AP
    LS --> M
    LA --> A
    M --> D
    M --> DL
```

---

## 7. Data Pipeline — 311 Delta Merge

```mermaid
flowchart TB
    Start([Container boot]) --> SH[start.sh]
    SH --> DL[uv run python -m agent311.download_311]
    DL --> Exists{311_recent.csv<br/>exists?}
    Exists -- No --> Full[Full download:<br/>sr_created_date ≥ year-1 Jan 1<br/>100k rows per page]
    Exists -- Yes --> Latest[Read max sr_created_date]
    Latest --> Delta[Fetch rows newer than max]
    Delta --> Merge[Concat + dedupe by sr_number<br/>sort by sr_created_date desc]
    Merge --> Write[Write 311_recent.csv]
    Full --> Write
    Write --> Uvicorn[uv run uvicorn agent311.main:app]
    Uvicorn --> Ready([Service ready])
```

---

## 8. Auth Flow

```mermaid
sequenceDiagram
    participant UI as Browser UI
    participant F as FastAPI
    participant Auth as auth.py

    UI->>F: POST /api/auth/login<br/>{email, password}
    F->>Auth: verify hardcoded creds
    Auth-->>F: ok
    Auth->>Auth: jwt.encode(sub=email,<br/>exp=+7d, iat=now)
    F-->>UI: { token }
    UI->>UI: localStorage["agentui-token"] = token

    Note over UI,F: Every subsequent request

    UI->>F: GET /api/* <br/>Authorization: Bearer <token>
    F->>Auth: get_current_user(token)
    Auth->>Auth: jwt.decode(JWT_SECRET, HS256)
    alt valid + not expired
        Auth-->>F: email
        F-->>UI: 200 response
    else invalid / expired
        Auth-->>F: 401
        F-->>UI: 401 Unauthorized
        UI->>UI: clear token → redirect /login
    end
```
