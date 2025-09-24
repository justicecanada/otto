# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                  |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/cache\_tiktoken.py                                             |        9 |        9 |      0% |      1-21 |
| django/chat/\_utils/estimate\_cost.py                                 |      145 |       23 |     84% |33, 43, 52-66, 70-72, 78, 80, 139, 161, 195-196, 208, 232, 247 |
| django/chat/\_views/load\_test.py                                     |       77 |       49 |     36% |42, 60-108, 116-137, 158-198 |
| django/chat/\_views/pin\_chat.py                                      |       47 |       31 |     34% |18-36, 45-52, 57-66 |
| django/chat/forms.py                                                  |      217 |       36 |     83% |54, 61, 148-163, 171-185, 204, 249, 257, 534, 620-622, 642-666 |
| django/chat/llm.py                                                    |      194 |       46 |     76% |49, 76-89, 114, 137-152, 155-157, 170-171, 175-180, 210-213, 299-302, 305-308, 366, 402 |
| django/chat/llm\_models.py                                            |       77 |        7 |     91% |80, 85-87, 92, 307, 321 |
| django/chat/models.py                                                 |      393 |       62 |     84% |81, 124, 185-189, 192-196, 309-312, 317-323, 331, 416, 484-488, 492, 496-500, 506, 512, 518, 550, 588-592, 644, 648-650, 665, 676, 714, 718-737, 746-747, 756-757, 767-768 |
| django/chat/prompts.py                                                |        5 |        0 |    100% |           |
| django/chat/responses.py                                              |      476 |      261 |     45% |67, 82, 92, 101, 147, 180-185, 226-350, 368-406, 417, 426-427, 435, 484, 490-576, 637-643, 662-663, 668-729, 732-762, 818-821, 832-858, 868-869, 872-875, 998-1015, 1025-1047, 1051-1054, 1063-1064, 1075-1081, 1098, 1121-1189 |
| django/chat/tasks.py                                                  |      105 |       42 |     60% |28-61, 65-73, 112-119, 161-162, 165-170, 177 |
| django/chat/templatetags/chat\_extras.py                              |       20 |       13 |     35% |     18-33 |
| django/chat/utils.py                                                  |      515 |       94 |     82% |107-109, 150, 162-163, 172-176, 187-192, 197-198, 207, 256, 280, 282-283, 294, 296-312, 317-329, 379-395, 445-447, 510-511, 515-522, 530, 547-551, 899-900, 962, 964-969, 973, 1031-1040, 1047-1061 |
| django/chat/views.py                                                  |      622 |      181 |     71% |85-93, 109-111, 117-133, 169, 182, 191, 208-210, 213-215, 245-276, 290-297, 303, 336-357, 454-458, 490-571, 597-598, 632, 636, 700, 720, 771-775, 820-822, 847-848, 929-930, 940, 982, 1073-1077, 1086, 1144-1181, 1191-1192, 1201-1206, 1250-1264, 1339-1403 |
| django/import\_timer.py                                               |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                  |       77 |        7 |     91% |28-33, 42, 56-61, 70, 109 |
| django/laws/loading\_utils.py                                         |      290 |       83 |     71% |60-75, 131-135, 153, 186-189, 249, 267, 269, 271, 273, 291-292, 296-297, 299, 302, 304, 319-320, 322-323, 420-423, 433-451, 477-481, 493, 512, 564-565, 606-608, 702-820, 836, 843 |
| django/laws/loading\_views.py                                         |      106 |       17 |     84% |89-91, 170-173, 186, 250-260 |
| django/laws/management/commands/load\_laws\_xml.py                    |       97 |       55 |     43% |87-145, 157, 172, 174-175, 181-191 |
| django/laws/models.py                                                 |      232 |       36 |     84% |35, 78, 85-90, 112-116, 134-141, 149-154, 161, 188, 240-241, 318, 320, 333-341, 357, 436 |
| django/laws/prompts.py                                                |        4 |        0 |    100% |           |
| django/laws/search\_history/models.py                                 |       20 |        3 |     85% |37, 42, 46 |
| django/laws/search\_history/views.py                                  |       51 |       37 |     27% |15-37, 43-92, 99-104 |
| django/laws/tasks.py                                                  |      317 |      113 |     64% |48-51, 62, 128, 141, 143, 150, 166-169, 211-215, 224-225, 234-235, 287-301, 313-338, 351, 369-380, 400-406, 455-468, 508-509, 516-533, 546, 550-552, 555-557, 563-578 |
| django/laws/test\_retriever\_performance.py                           |       60 |       10 |     83% |60-62, 81-83, 106-108, 117 |
| django/laws/translation.py                                            |        5 |        0 |    100% |           |
| django/laws/utils.py                                                  |       98 |       13 |     87% |24-26, 44, 90, 109-115, 132-136, 169 |
| django/laws/views.py                                                  |      304 |      177 |     42% |82, 86, 99, 109, 117-118, 124-215, 227, 243, 281, 283, 288-290, 297-323, 337, 373, 381, 389, 398, 412-432, 439-447, 451-574, 581-655 |
| django/librarian/forms.py                                             |      101 |        4 |     96% |125-126, 211, 229 |
| django/librarian/models.py                                            |      337 |       47 |     86% |54-56, 124, 126, 134, 136, 138, 148, 173-175, 197, 251, 313-314, 319, 330-333, 408, 425-434, 438, 456, 490-492, 502-503, 509, 525, 556-557, 567-568, 578-579, 591-592 |
| django/librarian/tasks.py                                             |      117 |       44 |     62% |42-75, 82, 92, 105, 115, 123-125, 143-144, 147, 167-169, 180-183, 202-203 |
| django/librarian/translation.py                                       |        8 |        0 |    100% |           |
| django/librarian/utils/extract\_emails.py                             |      109 |       31 |     72% |58-72, 85, 87, 95-101, 119, 122, 131-143, 153, 155 |
| django/librarian/utils/extract\_zip.py                                |       68 |       12 |     82% |37-39, 50-59, 92 |
| django/librarian/utils/markdown\_splitter.py                          |      185 |       10 |     95% |72, 75-77, 88, 126, 140, 263, 273, 280 |
| django/librarian/utils/process\_document.py                           |       21 |        1 |     95% |        35 |
| django/librarian/utils/process\_engine.py                             |      547 |      107 |     80% |62-64, 89-98, 113, 184, 187, 193, 202-203, 207, 210, 213, 216, 223, 225, 227, 229, 231, 233, 237, 239, 241, 243, 278, 301, 303-305, 316, 318, 336-337, 353-364, 367-369, 386-412, 416-422, 432, 441-455, 500, 541-543, 589, 592-596, 602-606, 610, 658-659, 705, 839, 864, 875 |
| django/librarian/views.py                                             |      471 |      130 |     72% |37-41, 60-67, 126-147, 153, 169, 196-215, 230, 263, 325-326, 331, 365, 371, 389, 404-408, 437-438, 444-445, 463, 474-475, 478, 491, 495-499, 529, 536-538, 656, 661, 677-712, 749, 831-846, 850-895 |
| django/otto/celery.py                                                 |       16 |        1 |     94% |        99 |
| django/otto/context\_processors.py                                    |       18 |        4 |     78% |     10-13 |
| django/otto/forms.py                                                  |       76 |        4 |     95% |73, 75, 215-216 |
| django/otto/management/commands/delete\_empty\_chats.py               |       19 |        1 |     95% |        29 |
| django/otto/management/commands/delete\_old\_chats.py                 |       21 |        2 |     90% |    32, 36 |
| django/otto/management/commands/delete\_text\_extractor\_files.py     |       18 |        0 |    100% |           |
| django/otto/management/commands/delete\_translation\_files.py         |       27 |        0 |    100% |           |
| django/otto/management/commands/delete\_unused\_libraries.py          |       21 |        2 |     90% |    32, 36 |
| django/otto/management/commands/reset\_app\_data.py                   |      122 |       18 |     85% |70-75, 90, 107-112, 132-137, 151-152, 157-160, 175-180, 191 |
| django/otto/management/commands/test\_laws\_query.py                  |       52 |       38 |     27% |18-121, 128-135 |
| django/otto/management/commands/update\_exchange\_rate.py             |       19 |        0 |    100% |           |
| django/otto/management/commands/warn\_libraries\_pending\_deletion.py |       26 |        3 |     88% |     29-33 |
| django/otto/models.py                                                 |      296 |       30 |     90% |28-30, 89-92, 125, 129-132, 167, 213, 216, 232, 253, 271, 397, 400, 455, 462, 490, 494, 501, 507, 556-557, 571, 575, 579, 602 |
| django/otto/rules.py                                                  |      175 |       15 |     91% |28, 46, 53, 55, 117-119, 124-126, 154, 222-224, 271 |
| django/otto/secure\_models.py                                         |      248 |       94 |     62% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 429-434, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                               |      171 |       24 |     86% |46-49, 59-60, 225-234, 308, 321, 380-387, 419, 509-510, 560 |
| django/otto/tasks.py                                                  |       57 |       17 |     70% |11, 33, 53, 67, 72-75, 80-88, 96-98 |
| django/otto/templatetags/filters.py                                   |       16 |        4 |     75% | 10, 23-25 |
| django/otto/templatetags/tags.py                                      |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                            |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                             |       37 |        9 |     76% |14-28, 65-67 |
| django/otto/utils/common.py                                           |       70 |        4 |     94% |102, 131-133 |
| django/otto/utils/decorators.py                                       |       64 |        4 |     94% |25-26, 67, 90 |
| django/otto/utils/logging.py                                          |       15 |        0 |    100% |           |
| django/otto/utils/middleware.py                                       |       54 |        6 |     89% | 32, 93-97 |
| django/otto/views.py                                                  |      639 |      152 |     76% |60, 65-66, 71-85, 120, 134, 145-155, 168, 304-305, 406, 423, 472-475, 491-492, 517, 527-535, 566-576, 588-593, 596, 605, 607-610, 612-613, 615-618, 641, 649, 658, 674-685, 791-792, 823, 825, 827, 841, 843, 850-851, 854-857, 867-873, 883, 885, 887, 892-912, 951, 960-969, 1048, 1056-1062, 1085-1086, 1100-1103, 1114, 1130-1133, 1145, 1178-1201, 1205-1213, 1237-1242, 1295-1297, 1317-1320 |
| django/postgres\_wrapper/base.py                                      |        6 |        0 |    100% |           |
| django/text\_extractor/models.py                                      |       18 |        1 |     94% |        29 |
| django/text\_extractor/tasks.py                                       |      104 |       61 |     41% |34-131, 163, 190, 198-214 |
| django/text\_extractor/utils.py                                       |      130 |       32 |     75% |58-81, 117-121, 171-172, 185-191 |
| django/text\_extractor/views.py                                       |      163 |       45 |     72% |49, 67-75, 83-86, 109-131, 144-165, 180, 184, 192-213, 218, 223-228, 251, 257-258, 280-281, 304-306, 314 |
|                                                             **TOTAL** | **9258** | **2369** | **74%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/justicecanada/otto/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/justicecanada/otto/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fjusticecanada%2Fotto%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.