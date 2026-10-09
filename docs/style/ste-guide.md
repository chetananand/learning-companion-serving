# Writing rules (ASD-STE100)

All text in this project uses ASD-STE100 Simplified Technical English (STE). This rule applies to the spec, `DESIGN.md`, the report, the slides, code comments, and commit messages.

## Rules that we apply

1. Write one topic in each paragraph. Use a maximum of 6 sentences in a paragraph.
2. Write descriptive sentences with a maximum of 25 words.
3. Write instructions with a maximum of 20 words. Write one instruction in each sentence.
4. Use the imperative form for instructions. Put a condition before the instruction ("If the pod is cold, do not send traffic.").
5. Use the active voice. Use the passive voice only when the agent is not known or not important.
6. Use only the simple tenses: simple present, simple past, and simple future.
7. Do not use the -ing form of a verb as a noun. You can use technical names, for example "prefix caching" and "chunked prefill".
8. Do not use contractions. Write `do not`, not `don't`.
9. Do not use semicolons. Make two sentences.
10. Use a maximum of three nouns in a noun cluster. If a name is longer, use "of" or a hyphen.
11. Use one word for one meaning. Do not use synonyms for a technical name.
12. Use a vertical list for complex text.
13. Write warnings and cautions as a command. Put the reason after the command.

## Words that we use

<!-- ste-ignore-start -->
| Do not use | Use |
|---|---|
| utilize, leverage | use |
| ensure | make sure |
| perform, carry out | do |
| provide | give, supply |
| require, required | is necessary, must, necessary |
| obtain | get |
| demonstrate, indicate | show |
| determine | find, calculate |
| modify | change |
| maintain, retain | keep |
| additional | more, other |
| prior to | before |
| in order to | to |
| due to | because of |
| whether | if |
| via | through |
| e.g., i.e., etc. | for example, that is, and other ... |
| should, would, could | must, can, or a direct statement |
<!-- ste-ignore-end -->

## Technical names

Technical names do not change. Examples: `max_num_seqs`, prefix caching, chunked prefill, prefill pool, decode pool, KV cache, NIXL, Mooncake, HAMi, KEDA. We write the names of code objects in backticks.

## The linter

`tools/ste_lint.py` checks these rules and the word list above. It is a helper, not the official ASD-STE100 checker. The official dictionary is not in this repository. A human review is still necessary.

```bash
python3 tools/ste_lint.py docs/ DESIGN.md
```

Fix all errors (codes that start with E). Read all warnings (codes that start with W). Change the text if the warning is correct.
