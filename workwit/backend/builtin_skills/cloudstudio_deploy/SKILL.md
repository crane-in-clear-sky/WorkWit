---
name: "cloudstudio_deploy"
display_name: "静态站点云部署"
description: "Deploy static sites to CloudStudio sandbox workspaces or take published sites offline. This skill should be used when users want to deploy a local build directory (e.g. dist/, build/, out/) to a CloudStudio workspace, preview or publish a static site with a shareable URL, or unpublish an existing deployment."
category: "util"
trigger_words: "部署,deploy,发布网站,静态站点,上线,部署到云端"
skill_type: "method"
when_to_use: "用户要求把本地构建的静态网站发布上线或下线时"
allowed_tools: []
source_name: "cloudstudio-deploy"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向 CloudStudio 云端部署平台，当前系统未接入该平台；若用户需要部署静态站点，说明当前能力边界，不执行 npm/CLI 命令。

# CloudStudio Deploy

Deploy local static sites to CloudStudio sandbox workspaces or take published sites offline via the built-in `workbuddy_cloudstudio_deploy` tool.

## When to Use

- User asks to **deploy**, **preview**, or **publish** a static site / web application
- User asks to **unpublish**, **take offline**, or **remove** a published static site
- User has a build output directory (e.g. `dist/`, `build/`, `out/`) and wants a live URL
- User mentions **CloudStudio**, **sandbox**, or **cloud preview**

## Limitations

- Only **pure front-end static sites** are supported (HTML/CSS/JS/assets).
- Server-side rendering (SSR), backend APIs, databases, or runtime dependencies (e.g. Node.js server apps) are NOT supported.
- If the project requires a backend or SSR, inform the user that only static site deployment is currently supported.

## How to Deploy

Use the built-in tool `workbuddy_cloudstudio_deploy`. It accepts:

- `action` (optional) — `deploy` (default) / `unpublish`
- `directory` (deploy required) — absolute path to the build output directory
- `port` (deploy only) — port for the static file server, defaults to 3000
- `entry` (optional) — entry HTML file; for unpublish, it can narrow a directory match
- `shareLink` (unpublish only) — published share link, preferred for precise targeting

## Workflow

When a user asks to deploy a site, follow this workflow in order. Be **conservative** — only proceed with build/deploy when you have high confidence in the result.

### Step 1: Identify the deploy target

Check if the user specified a directory. If yes, verify it looks deployable (has `index.html`). If it does, skip to Step 4.

If no directory is specified, or the specified directory does not contain `index.html`, proceed to Step 2.

### Step 2: Scan for existing build output

Look in the project root for common front-end build output directories. **Only check these whitelisted directories** (do NOT recursively scan the entire project):

- `dist/`
- `build/`
- `out/`
- `output/`
- `public/`
- `.next/out/` (Next.js static export)
- `.output/public/` (Nuxt static)
- `_site/` (Jekyll/11ty)
- `www/`
- `docs/`

For each found directory, check if it contains `index.html`. If exactly one candidate is found, use it. If multiple candidates exist, ask the user to choose.

### Step 3: Attempt to build (conservative strategy)

If no existing build output is found, check if the project has a buildable front-end:

1. **Check for `package.json`** in the project root. If missing, skip to Step 3c.

2. **Look for a build script.** Check `package.json` for these script names (in order of priority):
   - `build`
   - `build:prod`
   - `build:production`
   - `generate` (Nuxt)
   - `export` (Next.js)

   If a build script exists:
   - Check if `node_modules/` exists. If not, inform the user that dependencies need to be installed first and ask for confirmation before running `npm install` (or the appropriate package manager based on lock files).
   - Run the build script (e.g. `npm run build`).
   - After build completes, re-scan the whitelisted directories from Step 2 for `index.html`.
   - If a deployable directory is found, proceed to Step 4.
   - If the build fails or produces no static output, report the error to the user and stop.

3. **No package.json or no build script:**
   - Check if the project root itself contains `index.html` (simple static site with no build step).
   - If yes, use the project root as the deploy directory.
   - If no, inform the user: "No deployable static site found. This project may require a build step or may not be a static front-end project."
   - **Do NOT attempt to guess or construct build commands.** Stop and ask the user for guidance.

### Step 4: Deploy

Call the `workbuddy_cloudstudio_deploy` tool with the identified directory:


### Step 5: Report result

The tool returns a JSON with `shareLink` and `verified` fields.

- Present the `shareLink` as the **分享链接** to the user.
- Do NOT mention expiration, spaceKey, data plane URL, webIDE URL, or any other internal details.
- If `verified` is `false`, suggest the user wait a few seconds and try the link again.

## Important Rules

- Only show the `shareLink` to the user, referred to as **分享链接**.
- Do NOT mention expiration, validity period, spaceKey, data plane URL, webIDE URL, or any other internal details to the user.
- Be **conservative** in build attempts — only run build commands when there is a clear, standard build script present. Never fabricate or guess build commands.
- If analysis becomes too complex (e.g. monorepo with unclear structure, unconventional build setup), stop and ask the user for clarification rather than guessing.
- The tool handles everything internally: workspace creation, file upload, static server setup, and link generation.

## Unpublish (Take Offline)

Take a previously deployed site offline via the built-in `workbuddy_cloudstudio_deploy` tool with `action: "unpublish"`. After unpublishing, the site's **分享链接** stops working. This only cancels the artifact release; it does NOT destroy the sandbox and is reversible by redeploying.

### When to Unpublish

- User asks to **下线 / 取消发布 / 撤下 / 删除** a deployed site, or in English to **unpublish / take offline / take down / remove** a published app.

### How to Unpublish

Use the built-in tool `workbuddy_cloudstudio_deploy` with `action: "unpublish"`. The targeting parameters are optional; provide the most specific one available:

- `shareLink` (optional) — the share link of the deployment to take offline. **Preferred**, most precise.
- `directory` (optional) — absolute path of the directory that was deployed. Used when the share link is unavailable.
- `entry` (optional) — entry HTML file name, used with `directory` for precise matching.

Target resolution priority: `shareLink` → `directory` (+ `entry`) → otherwise the most recent still-published deployment in the current workspace.

### Workflow

1. Determine which deployment to take offline:
   - If the user provided or referenced a share link, pass it as `shareLink`.
   - Otherwise, if the user points to a project directory, pass it as `directory`.
   - If the user just says "下线刚才那个 / take the last one offline" without specifics, pass only `action: "unpublish"` and omit the targeting parameters to fall back to the most recent published deployment.
2. Call `workbuddy_cloudstudio_deploy` with `action: "unpublish"`.
3. Report the result:
   - On success, confirm the site has been taken offline and its **分享链接** no longer works.
   - On failure (e.g. no matching deployment found), tell the user no matching deployment was located and ask them to provide the share link or the deployed directory.

### Important Rules

- Do NOT expose conversationId, spaceKey, or any internal identifiers to the user.
- If multiple deployments could match and the target is ambiguous, ask the user to confirm (e.g. by share link) rather than guessing.