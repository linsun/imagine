# The platform agent

An on-call agent for the demo, on kagent's declarative runtime (no image of
ours), in namespace `kagent`.

| File | What |
| --- | --- |
| `harness.yaml` | `Harness` `platform`: kagent's runtime, on kagent's own worker pool, so it stays up when the `imagine` pool is full |
| `github-mcp.yaml` | `RemoteMCPServer` `github`: GitHub's hosted MCP server, token from Secret `platform-github` |
| `agent.yaml` | `ModelConfig` `platform-model` (OpenAI gpt-4.1, kagent's key, not via agentgateway) and the `Agent` with its prompt and tool list |

```bash
scripts/k8s-platform.sh          # Secret from .env, then apply
scripts/k8s-platform.sh status
scripts/k8s-platform.sh down
```

What it may do:

- **Read** pods, events, logs, YAML, Helm releases anywhere. No approval.
- **Change** the cluster (patch, rollout, scale, delete): each call waits for
  your approval in the kagent UI. This is how it raises `llm-token-limit`.
- **GitHub**: read linsun/imagine, push to a new `platform-agent/...` branch,
  open a PR against `kubecon-na`. It never writes to `kubecon-na` itself.

Its model does not go through agentgateway :3000 on purpose: the token limit
there is the demo knob, and the agent that raises it must not hit it.

kagent-tools runs as cluster-admin, so the `tools:` list in `agent.yaml` is
the permission boundary. Don't add `shell` or `k8s_execute_command`.
