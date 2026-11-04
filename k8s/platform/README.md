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
  your approval in the kagent UI.
- **GitHub**: read linsun/imagine, push to a new `platform-agent/...` branch,
  open a PR against `kubecon-na`. It never pushes to `kubecon-na` itself.
- **Merge** (`merge_pull_request`): waits for your approval in the kagent UI.
  Its prompt allows it only for its own token-limit PR; other fix PRs are
  yours to merge.
- **Refresh Argo CD** (`k8s_annotate_resource`, no approval): only
  `argocd.argoproj.io/refresh` on the `imagine-agents` Application.

## The token limit, by pull request

"raise the token limit to 200000" in the kagent UI:

1. It reads `k8s/agents/token-limit.yaml` on `kubecon-na`, changes only
   `tokens:` on branch `platform-agent/token-limit-200000`, and opens a PR.
2. It merges it (squash). **Approve the merge** in the kagent UI.
3. It annotates the Argo CD Application `imagine-agents` to refresh, so Argo
   CD applies the merge commit in seconds rather than on its ~3 minute poll.
4. It checks the Application is on the merge commit and reads back the live
   `llm-token-limit`, then replies with the PR URL and the new limit.

If GitHub or Argo CD fails, it offers the old way: a direct patch of
`llm-token-limit` (approval needed), which leaves git and the cluster apart
until the next commit. `scripts/k8s-argocd.sh status` shows both sides.

Needs: Argo CD with `k8s/argocd/imagine-agents.yaml` applied
(`scripts/k8s-argocd.sh`), the `k8s/agents/` layout pushed to `kubecon-na`,
and a GitHub token that may merge (Contents and Pull requests read & write;
branch protection on `kubecon-na` that requires reviews would block it).
After the show `kubecon-na` holds the raised limit: ask for "reset it" (200)
or commit it back.

Its model does not go through agentgateway :3000 on purpose: the token limit
there is the demo knob, and the agent that raises it must not hit it.

kagent-tools runs as cluster-admin, so the `tools:` list in `agent.yaml` is
the permission boundary. Don't add `shell` or `k8s_execute_command`.
