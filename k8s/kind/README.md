# kind cluster for Agent Substrate

The same result as `hack/create-kind-cluster.sh` in github.com/linsun/substrate,
written out as config you can read.

| File | What it does |
|---|---|
| `kind-config.yaml` | Feature gates and `runtimeConfig` for the certificate APIs, parallel image pulls, and a mount of the registry routing |
| `certs.d/localhost:5001/hosts.toml` | Tells containerd in the node that `localhost:5001/...` means the `kind-registry` container |
| `node-prep.yaml` | A DaemonSet that sets `proxy_arp`/`proxy_ndp` and sets them again after restarts, plus the `local-registry-hosting` ConfigMap |

## Create

```bash
# once: the local registry (skip if `docker ps` already shows kind-registry on :5001)
docker run -d --restart=always --name kind-registry -p 127.0.0.1:5001:5000 registry:3

kind create cluster --name kind --config k8s/kind/kind-config.yaml

# once per new cluster: the registry must share the kind Docker network
docker network connect kind kind-registry 2>/dev/null || true

kubectl --context kind-kind apply -f k8s/kind/node-prep.yaml
```

Then install substrate from the fork: `hack/install-ate-kind.sh`. It targets
`kind-kind` by default.

## Tested

Tested on 2026-09-24 with kind v0.33.0 (node image v1.37.0, containerd 2.3.4)
on Docker Desktop, arm64:
- The API server has the feature gates, and `certificates.k8s.io/v1beta1` is served.
- kubelet has `serializeImagePulls: false` and `maxParallelImagePulls: 4`.
- The node pulls `localhost:5001/<image>@sha256:...` from the local registry.
  Substrate needs this, because it references images only by digest.
- After `docker restart` of the node, `proxy_arp` was 0 and the DaemonSet set it
  back to 1 within about 9 seconds.

## Not included, on purpose

- **`/dev/kvm`:** Docker Desktop has no KVM, so there are no micro-VM
  WorkerPools. gVisor works without it.
- **IPv6 / dual-stack:** only IPv4, the substrate script's default.
