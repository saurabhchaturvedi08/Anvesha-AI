# Release checklist for Anvesha

This checklist covers the public launch preparation for the v0.1.0 milestone.

## Release readiness

- [ ] Repository documentation is current and consistent
- [ ] README explains the product, setup, and known limitations
- [ ] CONTRIBUTING guide reflects required validation steps
- [ ] Security guidance is available and current
- [ ] Changelog reflects the public release content
- [ ] CI checks are passing for backend tests and desktop build

## Packaging validation

- [ ] Backend tests pass locally
- [ ] Desktop production build passes
- [ ] Windows NSIS installer builds successfully
- [ ] Windows MSI installer builds successfully
- [ ] App starts successfully in debug mode or packaged mode
- [ ] No required secret or sensitive config is committed

## Release notes

- [ ] Highlight the major features included in the release
- [ ] Document any known gaps, limitations, or unsupported flows
- [ ] State supported platforms and prerequisites clearly
- [ ] Call out local model and provider configuration requirements
- [ ] Include installation or upgrade steps for end users
- [ ] Publish the v0.1.0 release notes in the public release section

## Public launch steps

1. Confirm the release version in the package and app metadata.
2. Ensure changelog content matches the actual shipped build.
3. Validate the Windows installers in a clean environment.
4. Tag the repository and publish the release announcement.
5. Share the release notes and known issues in the public communication.

## Known limitations for v0.1.0

- Local-first document and source handling is intentionally simple and offline-first.
- Search and model provider setup still depends on user environment configuration.
- Packaging is validated for Windows installer builds; additional OS-specific signing may be required before broader distribution.
- Evidence-backed research remains explicit and source-anchored rather than fully autonomous.
