# Changelog

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
