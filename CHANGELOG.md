# Changelog

## 1.2.0

- Add a final setup and options step to choose Home Assistant, Listonic, Google
  Keep or manual conflict resolution. Ordinary changes still synchronize both
  ways; automatic preferences also cover conflicting deletions.
- Keep unresolved products on hold across both Shopping List bridges while
  continuing to synchronize unrelated products.
- Add native Home Assistant Repairs for grouped conflict resolution, with a
  preview, explicit confirmation and another review if the lists change.
- Persist conflict decisions across reloads, verify the preferred source before
  writing, and defer decisions when its connection or stable ID mapping is
  unavailable. Uncertain writes still require separate verification.
- Preserve both providers' product identities and Listonic metadata when a
  conflict decision recreates a deleted Home Assistant item, and wake the other
  bridge as soon as a held product is released.
- Match unique, normalized names when initially merging independent sources and
  before importing unmatched products. Repeated names remain distinct, and
  already mapped products are excluded from import matching.
- Give synchronization sensors provider-specific entity IDs and labels. Migrate
  default generated IDs when the destination is free, preserving custom IDs.
- Expand Google Keep synchronization logging with lifecycle, queue, snapshot and
  retry details without logging product names or credentials.
- Extend English and Polish UI text, user documentation, backend regressions and
  Chromium coverage of the configuration and Repairs flows.

### Upgrade notes

- Existing configurations default to manual resolution. Select a preferred source
  in the final options step to enable automatic resolution.
- Generated sensor IDs can change to
  `sensor.lists_assistant_listonic_synchronization` and
  `sensor.lists_assistant_google_keep_synchronization`. Check dashboards and
  automations referencing the previous generated IDs; custom IDs are retained.
- Keep existing synchronization storage files. No mapping reset is required.

## 1.1.0

- Add Google Keep checkbox-list synchronization through the shared Home Assistant
  Shopping List, including durable mappings, offline writes and conflict handling.
- Let users select Listonic, Google Keep or both during setup, then choose todo
  lists and Shopping List synchronization targets independently.
- Add actions to synchronize all connected providers or one provider on demand.
- Make the polling interval configurable in minutes; it defaults to one minute.
- Use readable, provider-prefixed todo entity IDs and improve integration naming.
- Add setup guidance and acknowledgements for the projects and APIs this
  integration builds on.

## 1.0.2

- Batch Shopping List imports to reduce repeated full-list reads during large syncs.

## 1.0.1

- Allow the selected Listonic shopping-list target to be the only selected list
  in the configuration flow.

## 1.0.0

First release of Lists Assistant for Home Assistant, connecting Listonic lists
with native todo entities and the Home Assistant Shopping List.

- Account configuration, list selection and reauthentication through the UI.
- Two-way Shopping List synchronization with Assist support.
- Persistent mappings, offline queue, conflict and uncertain-write resolution.
- Actions to manage lists and products, including quantity, unit, description
  and price.
- English and Polish translations, synchronization sensor and diagnostics.
- HACS metadata and HACS/Hassfest validation in CI.

### Breaking change from development builds

The integration domain changed from `listonic` to `lists_assistant`. Action names
now start with `lists_assistant.`. The Listonic API and the conflict resolution
value `choose: listonic` are unchanged.

Existing development installations require manual reconfiguration. There is no
automatic migration of config entries, entity registrations or pending writes.
Follow [the upgrade instructions](README.md#przejście-z-testowej-integracji-listonic)
before enabling synchronization under the new domain.
