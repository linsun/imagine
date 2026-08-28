#!/usr/bin/env bash
# Pinpoint exactly where GitHub auth breaks. Run it after every token change.
#
# The codes mean different things and need different fixes:
#   401  token itself rejected      -> expired / revoked / mistyped
#   403  token valid, no permission -> wrong scopes on a repo it CAN see
#   404  token cannot SEE the repo  -> wrong repo name, or a fine-grained token
#                                      whose resource owner is not the repo owner
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

ok()  { echo -e "  \033[32mok\033[0m   $1"; }
bad() { echo -e "  \033[31mfail\033[0m $1"; }
hint(){ echo -e "       \033[2m$1\033[0m"; }

echo
echo "1. what are we even pointing at?"
if [ -z "${GITHUB_REPO:-}" ]; then
  bad "GITHUB_REPO is EMPTY in this shell"
  hint "that makes the URL /repos//releases, which is always a 404."
  hint "check ./.env has: GITHUB_REPO=owner/repo   (no quotes)"
  exit 1
fi
case "$GITHUB_REPO" in
  */*) ok "GITHUB_REPO = '$GITHUB_REPO'" ;;
  *)   bad "GITHUB_REPO = '$GITHUB_REPO' is not owner/repo"; exit 1 ;;
esac
case "$GITHUB_REPO" in
  *" "*|*'"'*|*"'"*) bad "it contains a space or quote -- that will 404"; hint "value: [$GITHUB_REPO]" ;;
esac
[ -n "${GITHUB_TOKEN:-}" ] && ok "GITHUB_TOKEN present (${#GITHUB_TOKEN} chars)" \
  || { bad "GITHUB_TOKEN empty in this shell"; exit 1; }

echo
echo "2. is the token itself valid?"
u=$(curl -s -w '\n%{http_code}' -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user)
code=$(echo "$u" | tail -1)
case "$code" in
  200) login=$(echo "$u" | sed '$d' | grep -o '"login": *"[^"]*"' | head -1 | cut -d'"' -f4)
       ok "authenticated as: $login"
       scopes=$(curl -s -I -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user \
                | tr -d '\r' | grep -i '^x-oauth-scopes:' | cut -d' ' -f2-)
       if [ -n "$scopes" ]; then ok "classic token, scopes: $scopes"
       else ok "fine-grained token (no scopes header)"
            hint "fine-grained tokens can ONLY reach repos owned by their chosen"
            hint "resource owner. Being a collaborator is not enough." ; fi ;;
  401) bad "401 -- token rejected. Expired, revoked or mistyped. Regenerate."; exit 1 ;;
  *)   bad "$code from /user"; exit 1 ;;
esac

echo
echo "3. can it see the repo?"
r=$(curl -s -w '\n%{http_code}' -H "Authorization: Bearer $GITHUB_TOKEN" \
      "https://api.github.com/repos/$GITHUB_REPO")
code=$(echo "$r" | tail -1)
case "$code" in
  200) owner=$(echo "$r" | sed '$d' | grep -o '"login": *"[^"]*"' | head -1 | cut -d'"' -f4)
       push=$(echo "$r" | sed '$d' | grep -o '"push": *true' || true)
       ok "repo visible, owned by: $owner"
       [ -n "$push" ] && ok "token has PUSH access" || { bad "token can read but NOT write"; \
         hint "fine-grained: Contents=Read and write, Pull requests=Read and write"; }
       if [ -n "${login:-}" ] && [ "$owner" != "$login" ]; then
         hint "note: repo owner ($owner) != token owner ($login)."
         hint "if this is a fine-grained token, that combination cannot work."
       fi ;;
  404) bad "404 -- this token cannot SEE $GITHUB_REPO"
       hint "either the name is wrong, or it is a fine-grained token whose"
       hint "resource owner is not '${GITHUB_REPO%%/*}'."
       hint "fix: create the token from the ${GITHUB_REPO%%/*} account,"
       hint "     or use a classic PAT with the 'repo' scope,"
       hint "     or point GITHUB_REPO at a repo the token's owner owns."
       exit 1 ;;
  *)   bad "$code looking up the repo"; exit 1 ;;
esac

echo
echo "4. can it actually create a release?"
t=$(curl -s -w '\n%{http_code}' -X POST -H "Authorization: Bearer $GITHUB_TOKEN" \
      "https://api.github.com/repos/$GITHUB_REPO/releases" \
      -d '{"tag_name":"agentgateway-auth-test","name":"auth test","body":"delete me"}')
code=$(echo "$t" | tail -1)
case "$code" in
  201) id=$(echo "$t" | sed '$d' | grep -o '"id": *[0-9]*' | head -1 | grep -o '[0-9]*')
       ok "created a release -- publishing will work"
       curl -s -X DELETE -H "Authorization: Bearer $GITHUB_TOKEN" \
         "https://api.github.com/repos/$GITHUB_REPO/releases/$id" >/dev/null
       ok "cleaned the test release up" ;;
  403) bad "403 -- valid token, insufficient permission"
       hint "fine-grained: Contents = Read and write" ;;
  404) bad "404 on create -- see step 3's hints" ;;
  422) ok "422 -- tag already exists, which still proves write access" ;;
  *)   bad "$code: $(echo "$t" | sed '$d' | head -c 200)" ;;
esac
echo
echo "  after any token change:  make down && make up"
echo
