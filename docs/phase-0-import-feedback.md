# Phase 0 Import Feedback

Issues found while importing real recipes in Task 9 (T0.8). The first list was fixed during the import. The second list is still open: each item needs a decision on whether to fix it, and in which phase. The design has no task for the usability of the Phase 0 pages; Phase 1's T1.9 covers only the new chat and plan pages.

## Fixed during the import

1. **Page layout:** base styles for every page (color tokens with dark mode, page width, headings, controls, cards, row lists), a header that highlights the current page, and card layouts for the import, review, library, inventory, people, and dictionary pages.
2. **Review form layout:** one field per row, ingredient lines as cards with unmatched lines highlighted, delete kept apart on the right, and Save with Discard (or Cancel when editing) in a bar fixed to the bottom of the window.
3. **Skip wording:** an ingredient's "Leave unlinked" says the line is saved with unknown allergens; cookware's "Not needed" says it is not saved and not checked.
4. **Time source:** the user can set it. A changed time defaults to "entered by you" and can be marked "stated in the source"; "estimated" is offered only while the model's number is unchanged.
5. **Undo a changed link:** "Change link" can be undone until the line is linked, created, or left unlinked.
6. **Adding ingredients and cookware:** both are added from a dictionary search. A name the dictionary already has is not offered as a new entry. Typed wording is kept (土豆丝 linked to 土豆); text in the other language or a fragment typed while searching becomes the entry's name. Cookware not in the dictionary opens its create form at once, because unlinked cookware is never saved.
7. **Added lines:** marked "(added by you)", and their raw text always equals their name.
8. **Bug: a recipe with an added line could not be saved,** because the added line's raw text was empty.
9. **Search ranking:** an exact name or alias match comes first, so the 20-result limit never hides it. Out-of-order search responses are ignored.
10. **Allergen suggestions:** one request per name at a time, since development mode had sent two paid calls.
11. **Dictionary page:** edit user ingredients (names, aliases, category, stock mode, allergens), browse every entry with paging, and see how many dishes use each user entry. Deleting a used entry says what it affects.
12. **Error messages:** codes that fell back to "Something went wrong" now say what happened.
13. **Extraction prompt:** numbering and symbols are removed from titles, untitled recipes get a name, and basic utensils (spoons, chopsticks, spatulas) are not listed as cookware. Changed before any evaluation log existed, so the version stays `extract_v1`.
14. **Model ID:** each extraction records the configured name and the model ID, such as `gemini:gemini-3.5-flash-lite`.
15. **Confirmations:** discarding a draft asks first. Every inline confirmation is a red box with a red confirm button.
16. **Import guidance:** the paste box and link box say they take one recipe or one page. Several links, or a paste that `---` lines split into more than one recipe, are stopped before any model call.
17. **Linked names:** "Linked to" shows the dictionary entry's own name (葱 for a line written 葱花). Renaming a linked line to another entry's name warns and offers to relink.
18. **Source text during review:** shown beside the form; screenshot drafts say the image is not kept.
19. **Rights notice:** the import page asks the user to import only content they may use.
20. **Notice wording:** ten notices rewritten for clarity and tone, without assuming a friend's gender.
21. **Full method kept:** each recipe keeps its pasted text, batch part, or screenshots, per the storage principle in design 4.2, and the library shows it on request.
22. **Supplies and spoon sizes:** consumables such as plastic wrap and foil are a separate supplies list, shown as a reminder and never checked. Units gain tablespoon and teaspoon; a spoon whose size is not stated stays a spoon. A bowl, cup, handful, or pinch keeps its number with no unit. Amount accuracy now also counts household measures. The prompt changed before any evaluation log existed, so the version stays `extract_v1`.
23. **Review rule: the whole text is evidence.** Amounts and ingredients are read from the full source, not only the ingredient list. An ingredient that appears only in the method (the water and oil in a marinade) is added as a line, and a size word in the method, such as 一点点, 少许, or 少量, makes the amount `vague`, never `missing`. `missing` means the whole text says nothing about the amount. `raw` still holds the source's ingredient-list wording.
24. **Unit and amount type agree:** choosing a unit sets the amount type (grams and milliliters are exact, pieces and stalks are counts, spoons and a number with no unit are household measures). A line whose unit and type disagree, or whose amount is 0, is marked with a one-click fix, and save waits until it is fixed. The server rejects such lines too, so the evaluation set cannot hold them. A mouse wheel no longer changes an amount. Two saved lines with the wrong type and one salt amount turned to 0 were corrected in the recipe and its evaluation record.

25. **Choice groups:** ingredient lines gained a nullable `choice_group` integer; lines of one recipe sharing a value are alternatives, one of each group required, members optional together. The model splits "A（B）" and "A或B" into same-group lines and expands a generic word such as 水果 into the items the source itself suggests, deduplicated against lines it already output; a lone group member is cleared before review. In the review form, dragging one line onto another merges them into a "pick one" card (drag onto a card joins it; a drop zone below the card, or its "not a choice" button, takes a line out); the card carries one whole-group optional checkbox and a title that states whether the group is required; removing a line clears an orphaned partner. Splitting a dish stays the user's call: the model always returns one dish, and the rejected alternative (one dish per combination) is recorded in the plan.
26. **Whole-text amounts:** `missing` now means the whole text says nothing; amounts and ingredients stated in the method count (rule 23). The anti-fabrication guard used to clear a method-stated amount because the ingredient-list line carries no digits; it now also accepts an amount when the source states that very number (digits or Chinese numerals) next to the name, so "生抽4勺" in a step survives, while an invented number is still removed even when a neighbouring list entry or a step number sits nearby. Screenshot imports have no source text, so they keep the strict guard and a method-stated amount there is still cleared. Prompt examples were genericized away from the imported test recipes after an overfitting check: the amount rules generalized out of sample, and the fix was verified on real calls. The evaluation logs were deleted first, so the version stays `extract_v1`.
27. **Title version words:** the prompt shows that "（版本2）" is kept when numbering is removed.
28. **Language switch warns about drafts:** switching the language remounts the locale layout and silently dropped every queued draft, bypassing the reload warning. Found by the final branch review and reproduced in the app. The switch now asks inline first while drafts are queued; cancel keeps them.

## Still open

| Issue | Effect | Possible fix |
|---|---|---|
| Reloading the page loses the review queue | Unsaved drafts must be finished before a reload | Keep drafts in the browser's local storage |
| No progress during a batch import | The page looks stuck while it waits | Show "3 of 10 done" |
| Several recipes pasted without a `---` line are read as one | A dish can be lost or merged | Have the model report how many recipes it sees and warn, or split them |
| Built-in entries cannot take user aliases | Wording such as 马铃薯丁 must be linked by hand every time | Add the aliases to the seed dictionary, or let the user add personal aliases to built-in entries |
| Cookware entries have no edit form | They can only be deleted | Add an edit form; the API exists |
| Links without `http://` or `https://` are not recognized | `www.` links and app short links must be entered as the source by hand | Widen the link pattern |
| Web pages with under 200 characters of main text are rejected | A short Chinese recipe page can be refused | Lower the floor for Chinese pages |
| The model duplicates ingredients across the list, the method, and formula lines | 蛋 reappears as 鸡蛋 and as a 荷包蛋/水煮蛋 choice group, 吐司 as 全麦吐司, despite a dedup rule in the prompt; the duplicates survive out-of-sample tests, so this is the model's instruction-following ceiling | Delete the duplicates during review for now; revisit in `extract_v2` or with a stronger model, and let the evaluation metrics track it |
| Added lines count as lines the model missed | Evaluation blames the model for ingredients the source never had | Ask, when adding, whether the source has the ingredient |
| An allergen suggestion is requested again when its form reopens | A small repeated cost | Cache suggestions for the page session |
| 吐司 is an alias of 面包 in the seed dictionary | Toast links to bread | Decide whether toast is its own entry |
| No mobile layout or overall visual design | The pages have base styling only | A separate design pass |
