-- Week 2 document-ingestion categories (LabPDF, IntakeForm) -- run exactly once (guarded by a
-- row-count check in docker/entrypoint.sh, the same pattern used for the patient seed scripts).
--
-- Added as new rightmost children of the root "Categories" node (id=1) via the standard MPTT
-- "append rightmost child" operation: new nodes get lft/rght past every existing node, and only
-- the root's own rght needs to grow to cover them -- no other row's lft/rght changes.
--
-- Deliberately named WITHOUT spaces: DocumentService::isValidPath()/getLastIdOfPath() have a
-- pre-existing bug where the bound search parameter is normalized via str_replace("_","",...)
-- (strips underscores only) while the SQL side compares against replace(LOWER(name),' ','')
-- (strips spaces and lowercases) -- so any category name containing a space, including the
-- built-in "Lab Report"/"Medical Record", can never match in getAllAtPath() (confirmed by direct
-- reproduction; separately flagged for a real fix). Single-word names sidestep that bug entirely
-- without depending on it being fixed.
-- `categories.id` has no AUTO_INCREMENT (it's a manually-assigned primary key throughout this
-- table), so explicit next-available ids are required here too.
SET @root_rght = (SELECT rght FROM categories WHERE id = 1);
SET @next_id = (SELECT MAX(id) + 1 FROM categories);

INSERT INTO categories (id, name, value, parent, lft, rght, aco_spec)
VALUES (@next_id, 'LabPDF', '', 1, @root_rght, @root_rght + 1, 'patients|docs');

INSERT INTO categories (id, name, value, parent, lft, rght, aco_spec)
VALUES (@next_id + 1, 'IntakeForm', '', 1, @root_rght + 2, @root_rght + 3, 'patients|docs');

UPDATE categories SET rght = @root_rght + 4 WHERE id = 1;
