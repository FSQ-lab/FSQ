# Module: frontend

## Purpose

Own the repository frontend workspace, including browser dependency resolution, Vite development and production compilation, generated-asset policy, and navigation to independently owned frontend application modules.

This parent module does not own child application behavior or Python HTTP/runtime behavior. Child application contracts live in their own `SPEC.md` files, and the Python modules that serve generated assets or APIs own those server contracts.

## Dependencies

- The root `package.json` and `package-lock.json` own exact npm dependencies and the single lock-file dependency graph.
- Supported Node.js versions are `^20.19.0 || >=22.12.0`.
- Vite is the frontend development and production build tool.
- `ts-ebml` is the current browser runtime dependency used by the Control Plane child module for seekable WebM replay generation.
- `react-markdown` is the current browser runtime dependency used by the Control Plane child module for safe Markdown preview without raw HTML support.
- The workspace consumes the Python Control Plane HTTP origin only through Vite development proxy configuration and browser HTTP requests. Frontend source does not import Python modules.
- The Website child module is a static browser application with no backend transport or runtime API dependency.

## Public Interface

- `npm run dev` starts the Vite development server for the Control Plane entry and proxies `/api/control-plane` to `FSQ_CONTROL_PLANE_API_ORIGIN`, defaulting to `http://127.0.0.1:8879`.
- `npm run build` compiles the configured Control Plane page entry through the untracked `.frontend-dist` staging directory and distributes it into its owning Python package static directory.
- `npm run dev:website` starts the Website child entry with the same repository dependency graph and its GitHub Pages base-path behavior.
- `npm run build:website` compiles the Website child entry into `.frontend-dist/website` without distributing it into a Python package.
- `frontend/control-plane/SPEC.md` defines the Control Plane browser application's public behavior and source boundary.
- `frontend/website/SPEC.md` defines the public introduction website's behavior, canonical URL, and source boundary.

## Internal Structure

- `../package.json`: Root npm metadata, scripts, Node.js compatibility, and exact direct dependency versions.
- `../package-lock.json`: Complete locked npm dependency graph.
- `../vite.config.js`: Multi-page entry configuration, development API proxying, and production output mapping.
- `../vite.website.config.js`: Website development, project-site base-path, and deployment-output configuration.
- `control-plane/`: Independently specified Control Plane browser application.
- `website/`: Independently specified public introduction website.
- `../.frontend-dist/`: Untracked temporary Vite build staging.
- `../.frontend-dist/website/`: Untracked deployment-only Website output.
- `../fsq_agent/adapters/control_plane/static/`: Untracked generated Control Plane build output consumed by canonical Python packaging and production static serving; it is not authored frontend source.

## Frontend Architecture

- Workspace role: Build and dependency workspace, not a browser application architecture layer.
- Application defaults: New frontend application modules use React with TypeScript/TSX and the shared Vite workspace unless their confirmed module SPEC records a concrete exception.
- Child ownership: Each independently owned frontend application directory has one module `SPEC.md`. Ordinary implementation directories such as component, hook, style, and asset folders do not require separate specs unless they become independent ownership boundaries.
- Dependency direction: Child applications may use dependencies declared by the root npm workspace and may consume documented backend transport contracts. Backend modules do not import authored frontend source; they consume only generated assets at packaging or runtime boundaries.

## Error Handling

- Locked installation or Vite compilation failures fail the frontend build; the workspace does not fall back to vendored or remote browser bundles.
- Missing or incompatible browser dependency exports fail during compilation rather than degrading at browser runtime.
- Source-checkout production startup behavior for missing generated assets is owned by the Python module that serves each generated entry.

## Verification Scope

- A locked npm install and each Vite build produce the configured page entry and hashed assets under that entry's documented output boundary.
- A locked npm install and Website build produce a self-contained GitHub Pages artifact whose URLs resolve below the `/FSQ/` project-site base path.
- The npm manifest and lock file remain synchronized, and CI exercises the supported Node.js release lines.
- Generated assets, third-party bundles, and `node_modules` remain untracked.
- Installed-wheel verification confirms generated frontend assets are packaged without requiring Node.js at runtime.
- Frontend modules add type, lint, unit/component, browser, accessibility, or visual verification when those tools and obligations are present in their confirmed module specs.
- Website publication verification distinguishes local build success, Pages deployment success, and the canonical public URL serving the deployed revision.

## Current Invariants

- The repository has one root npm project and one npm lock file.
- Vite remains configured as a multi-page build even when only one application entry exists.
- Exact dependency versions are owned by the manifest and lock file, not by agent skill instructions.
- Authored frontend source lives under `frontend`; generated Control Plane assets live under its Python package static directory, generated Website assets live under `.frontend-dist/website`, and generated assets are not tracked.
- Each frontend application's framework, language, ownership, and public behavior must match its module SPEC.
- Website builds and deployment never copy, delete, or mutate Control Plane generated assets or Python distribution contents.
