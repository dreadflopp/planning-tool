# AI Coding Assistant Prompt

## Python + PySide6 Route Planning Desktop Application (Full Specification)

You are a senior desktop software architect.

I am building a local desktop application, it should work on both linux and windows. This is a suggested tech stack, if you find anything else more fitting, please use that instead:

-   Python 3.12+
-   PySide6 (Qt for Python)
-   SQLite (local persistence)
-   Google Distance Matrix API (travel time)
-   Excel import (.xlsx)

This application is a route planning tool for elderly care workers.

Performance, correctness, responsiveness, and clean architecture are
critical.

This is NOT a web application.

------------------------------------------------------------------------

# High-Level Concept

The UI has two main areas:

LEFT SIDE → Routes\
RIGHT SIDE → Visit Pool (grouped by street)

We will handle up to \~300 visits.

This is NOT a time-scaled scheduler.

All visit blocks have fixed height.

Routes are vertical ordered stacks:

\[Visit\] \[TravelTime or EmptySpace\] \[Visit\] \[TravelTime or
EmptySpace\] ...

The left and right areas size should be editable by dragging a separator between them. Use scrollbar to show content that doesn't fit it the area. Allow user to change font size to edit size of items making more or less data visable.

------------------------------------------------------------------------

# Excel Import Specification

Visits are imported from an Excel export file (.xlsx).

The Excel file header is:

ObjectID \| Keys \| PatLastName \| PatID \| WayDescription \| EmpID \|
Phone \| CellPhone \| NextOfKin \| KeyCode \| Grupp \| Nyckel \| Viktig
tid \| Starttid \| Sluttid \| Personnr. \| Nr. \| Namn \| Adress \|
Område \| Slinga \| Sign. \| Status \| Besökstyp \| Insatser \|
Beskrivning \| Gruppspecifik besöksinformation

------------------------------------------------------------------------

## Fields Required to Create a Visit

We ONLY use the following fields:

### Starttid

Visit start time. Example: 2026-02-23 17:22:00

Date is irrelevant --- only time-of-day is used.

### Sluttid

Visit end time. Same format. Date ignored.

### Namn

Person/customer name.

### Adress

Address string. We will remove postal code and city name. Postal code is 5 numbers with optional whitespace and postal code is followed by city name.

### Slinga

May contain a Swedish color keyword.

Recognized colors:

GRÖN → Green\
ROSA → Pink\
BLÅ → Blue

Case-insensitive matching.\
If no known color exists → no color assigned.

### Insatser

Stored as part of the visit.

### ObjectId

This is the visit key. We will ensure there are no duplicate keys and we will warn the user if there are 

------------------------------------------------------------------------

## Visit Filtering Rules

A visit must NOT be imported if the "Adress" field is empty or null

------------------------------------------------------------------------

## Import Requirements

-   Normalize times
-   Store visits in SQLite
-   Preserve original imported data
-   Allow safe re-import, ie match existing visits with the newly imported using ObjectId. Ask the user if we should remove the visits that are not present in the new import

Recommended libraries: openpyxl or pandas

------------------------------------------------------------------------

# Visit Pool (Right Side)

-   Visits grouped into columns by street.
-   Sorted by start time (informational only).
-   Fixed-height blocks.
-   Drag-and-drop enabled.

Each visit block displays:

-   Customer name on row 1, column 1
-   Address on row 2, column 1
-   Time range (HH:MM--HH:MM) on row 2, column 1. Allow user to edit. Edits updates Total visit duration.
-   Total visit duration on column 2. Span several rows, bigger and bolder. Allow user to edit. Add  to arrows, up and down, for quick edits. Edit updates time range end time to match duration.
-   Optional color
-   Insatser value, row 4, spans column 1 and 2
-   Up and down arrow that moves the visit up and down in column 3. 

------------------------------------------------------------------------

# Office Template Visit

There exists a default Office visit template.

-   Can be dragged into routes unlimited times.
-   Becomes a normal visit instance when placed.
-   Can be renamed (Start, Lunch, End of day).
-   Participates fully in travel calculations.
-   Name is editable, address is editable. Default address is editable by editing the template visit in the right section before placing it. 

------------------------------------------------------------------------

# Travel Segments

Between each pair of visits:

-   TravelTime block (yellow)
-   EmptySpace block (blue)
-   Or both logically

TravelTime block shows:
 
- Start time-end time
-   Duration
-   Travel mode selector

Supported modes:

Car, Bike, Walk

Travel mode stored per segment.

------------------------------------------------------------------------

# Travel Mode Behavior

A global selector exists above visit pool:

Default Travel Mode

When inserting a visit: new travel segments default to selected mode.

Changing travel mode recalculates immediately and autosaves.

------------------------------------------------------------------------

# Editable Visit Times

Each visit allows editing:

-   Start time
-   End time

Rules:

-   Duration preserved when reordered.
-   Duration displayed.
-   Editing recalculates adjacent segments.
-   Autosave immediately.

------------------------------------------------------------------------

# Empty Space Logic

If:

(next_visit.start - current_visit.end) \> travel_time

Create a blue EmptySpace block showing unused time.

------------------------------------------------------------------------

# Minimum Time Between Visits

Global setting:

Minimum Time Between Visits (default 2 minutes)

If travel_time \< minimum_time:

travel_time + empty_space ≥ minimum_time

Changing minimum time does not auto-modify routes.

Buttons required:

Apply Minimum Time To Routes\
Strip Extra Empty Space

------------------------------------------------------------------------

# Visit Reordering

Supported:

-   Drag and drop
-   Up/Down buttons

When moving UP:

-   Swap with visit above.
-   Moving visit takes replaced visit start time.
-   Duration preserved.
-   Empty space preserved.
-   Travel recalculated.
-   Autosave.

------------------------------------------------------------------------

# Column Reordering

Users can reorder:

-   Route columns
-   Street columns

Each column header has Move Left / Move Right buttons.

Must persist order without rebuilding scene.

------------------------------------------------------------------------

# Route Header

Each route column includes:

-   Editable route name
-   Expandable notes section
-   Autosave on edit

------------------------------------------------------------------------
# Filters
Each value in the column "Insatser" that we read during excel import should generate a checkbox above the visits and routes section. The values are comma separated. Unticking the box will grey out the visits that doesn't contain the keyword. The greying out should be significant.
------------------------------------------------------------------------
# Paired visits
If a visit contains the keyword "DUBBELGÅNG 1" or "DUBBELGÅNG 2" and there is another visit with the same name and address and their start time is within 20 minutes, they are a pair. If you select a visit that is part of a pair, the other visit should be highlighted.
------------------------------------------------------------------------
# Distance between visits and travel time
We should build a database for travel time between visits for all three travel types. It should not matter if the we travel from A to B or from B to A. Knowing this will reduce the size of the database. The database will grow as we place visits. We will have a button that build the whole database, making the experience smoother when we can use the cached data. We will show a status window that show requests and responses when calculating travel time. We will build the db using async calls.

When we select a cell and press a button we will show the closeness of other visits by displaying a number on them, 1 for the closest on, 2 for the next one and so one. How many we will show should be configurable by an input field next to the button. Doing this should only be possible if the database is pre built.

# Autosave

Triggered on:

-   Visit move
-   Reorder
-   Time edit
-   Travel mode change
-   Notes edit
-   Route rename
-   Column reorder

Autosave must be instant and silent using SQLite.

------------------------------------------------------------------------
# Export state

The user should be able to export the current state of the app, including all saved and placed visits routes, setting etc, everything. Cache is not included, it is global and reused between states.

# Export excel

The user can export to excel. One sheet shows the left area vith routenames and notes and visits and one sheet shows the right side with streetnames and visits.

# UI Technical Requirements

Recommendation:

-   QGraphicsView
-   QGraphicsScene
-   Custom lightweight QGraphicsItem subclasses: VisitItem TravelItem
    EmptySpaceItem RouteColumnItem VisitPoolColumnItem

Do NOT:

-   Use QWidget items inside scene
-   Rebuild entire scene
-   Place business logic in UI classes

------------------------------------------------------------------------

# Layout Engine

Fixed heights:

VisitHeight = constant\
GapHeight = constant

Position formula:

y = index \* (VisitHeight + GapHeight)

Only reposition affected items. Animate using QPropertyAnimation. Target
smooth 60fps interaction.

------------------------------------------------------------------------

# Database Schema Must Include

-   visits
-   routes
-   route_visit_order
-   travel_segments
-   settings
-   column_order
-   travel_time_cache
-   import_metadata

More if needed

------------------------------------------------------------------------

# Architecture Separation

UI Layer: Graphics scene and items only.

Domain Layer: Visit, Route, TravelSegment, EmptySpace

Services: ExcelImportService TravelTimeService PersistenceService
AutoSaveManager MinimumTimePolicyService

Controllers: DragDropController RouteLayoutEngine
RouteRecalculationEngine

Business logic must NOT exist in QGraphicsItem.

------------------------------------------------------------------------

# Deliverables

1.  Full project folder structure.
2.  Database schema.
3.  Domain models.
4.  Excel import implementation.
5.  Layout and recalculation algorithms.
6.  Minimal working prototype including:
    -   Mock import
    -   Visit pool
    -   One route
    -   Drag and drop
    -   Travel blocks
    -   Empty space logic
    -   Editable times
    -   Column reorder buttons
    -   Autosave stub

Focus on performance, maintainability, deterministic behavior, and clean
architecture.

If anything is unclear, ask clarifying questions before coding.
