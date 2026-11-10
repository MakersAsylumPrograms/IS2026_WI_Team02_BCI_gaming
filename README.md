# EqualCare BCI
Maker's Asylum Innovation School · 2026 Winter Diwali Cohort #8 · Team 2

EqualCare explores assistive communication for people with limited mobility or speech impairments. The proposed system uses eye-movement signals to navigate a patient request menu and communicate with caregivers.

**[Read the project wiki](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki)** for the solution overview, architecture, bill of materials, project records and implementation gaps.

## Proposed solution

Gel electrodes around the eyes feed a BioAmp EXG Pill. An ESP32 would detect eye gestures and send commands over Wi-Fi to a patient laptop. A staff laptop would display requests and exchange acknowledgements with the patient interface. A joystick provides a planned backup input.

The repository retains its original **BCI Gaming** name. The EqualCare hardware diagram specifies **EOG (eye-movement signals)**; EEG-based thought decoding is not established by the published material.

## Implementation status

| Area | Status supported by published material |
| --- | --- |
| Architecture and control flow | **Documented:** two design PDFs are available. |
| Components | **Procurement recorded:** the BoM lists parts and reports several as delivered. Delivery does not establish assembly. |
| Built functionality | **Not verified:** no firmware, application code, assembled-prototype demonstration or test results are published in the reviewed repository. |
| Eye-signal acquisition and gesture detection | **Planned:** shown in the hardware diagram; implementation and calibration are not published. |
| Patient request menu, staff dashboard and Wi-Fi acknowledgement | **Planned:** shown in the hardware diagram; no working integration is demonstrated. |
| Joystick backup and OLED feedback | **Planned:** described in the diagrams/BoM; operation is not verified. |
| Circuit, PCB and enclosure | **Not published:** Electronics and CAD contain placeholder READMEs. |

These labels describe the evidence available in this repository, including work that may have been completed offline but has not been documented here.

## Design documents

- [EqualCare hardware block diagram](Documentation/EqualCare%20%E2%80%93%20Hardware%20Block%20Diagram.pdf): EOG input, ESP32, patient/staff laptops, Wi-Fi and joystick backup.
- [EqualCare BCI flowchart](Documentation/EqualCare%20BCI%20-%20Flowchart.pdf): laptop game/application commands, OLED feedback and joystick correction.
- [Bill of materials](BCI_Gaming_BOM%20-%20Team_BoM.csv): recorded quantities, costs and order status.

The flowchart retains gaming/application behavior, while the hardware diagram describes caregiver communication. These design views still need reconciliation. The BoM and reference datasheets also differ on processor/display specifications; see the [project details](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/Project-Details) and [BoM notes](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/Bill-of-Materials-%28BOM%29).

**Safety requirement from the hardware diagram:** electrodes are used only on isolated battery power, with no USB or charger connected during human testing. This is a documented design constraint; no safety validation report is published.

## Repository contents

| Location | Current contents |
| --- | --- |
| [Documentation/](Documentation/) | EqualCare architecture and control-flow PDFs |
| [Code/](Code/) | Placeholder README; no firmware or application implementation |
| [Electronics/](Electronics/) | Placeholder README; no wiring schematic or PCB layout |
| [CAD/](CAD/) | Placeholder README; no mechanical models |
| [Photos_Videos/](Photos_Videos/) | Placeholder README and license graphics; no prototype photos or demo |
| [Reference_Data/](Reference_Data/) | Component datasheets for BioAmp, joystick, OLED and ESP32 |
| [BoM CSV](BCI_Gaming_BOM%20-%20Team_BoM.csv) | Parts list and procurement records |

## Wiki

- [EqualCare overview](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki)
- [Project details and implementation status](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/Project-Details)
- [Bill of materials](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/Bill-of-Materials-%28BOM%29)
- [Circuit and CAD files](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/Circuit-%26-CAD-Files)
- [Documentation guide](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/ReadME_1st)
- [Media gallery](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/Media-Gallery)
- [SDG alignment](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/SDG-Goals-Associated)
- [Licensing](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/License)

Daily records are linked from the wiki sidebar. Day 1 retains the existing training account; Days 2–6 are marked as undocumented.

## Remaining work

Reconcile the design documents and component selections; publish firmware, applications, wiring and build instructions; document calibration and demonstrate the actual request/acknowledgement and backup-input behavior. Accuracy, latency and usability results remain to be established through documented tests.

## Team

The BoM names Aarush Mehta, Avaneesh Devendra Phadte, Kanika Suri and Jiyaan Shah, with Shivam Kulkarni as mentor. Existing repository contact: [@anool](https://github.com/Anool).

The repository's cohort label above is retained from its original README; the BoM header instead says Cohort 1. This discrepancy is recorded in the wiki.

## License

- **Hardware:** [CERN Open Hardware License v2 - Strongly Reciprocal](https://spdx.org/licenses/CERN-OHL-S-2.0.html).
- **Software:** [MIT License](https://opensource.org/license/mit).
- **Documentation:** [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

These are the licenses stated by the original README. A standalone LICENSE.md is not present in the reviewed repository; see the [wiki licensing page](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/License). Supplier datasheets retain their own notices.
