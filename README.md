# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                  |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/cache\_tiktoken.py                                             |        9 |        9 |      0% |      1-21 |
| django/chat/\_views/load\_test.py                                     |       77 |       77 |      0% |     7-198 |
| django/chat/\_views/pin\_chat.py                                      |       47 |       31 |     34% |18-36, 45-52, 57-66 |
| django/chat/forms.py                                                  |      207 |       39 |     81% |46, 53, 140-155, 163-177, 196, 241, 249, 478, 480-482, 562-564, 584-608 |
| django/chat/llm.py                                                    |      192 |       54 |     72% |49, 76-89, 110, 133-150, 153-155, 168-169, 173-178, 194-211, 297-300, 303-306, 364, 386, 400 |
| django/chat/llm\_models.py                                            |       76 |        8 |     89% |76, 81-83, 88, 288, 292, 302 |
| django/chat/models.py                                                 |      348 |       62 |     82% |38, 81, 142-146, 149-153, 276-279, 284-290, 298, 406-410, 414, 418-422, 428, 434, 440, 472, 492, 510-514, 566, 570-572, 587, 598, 636, 640-659, 666-669, 679-680 |
| django/chat/prompts.py                                                |        5 |        0 |    100% |           |
| django/chat/responses.py                                              |      442 |      191 |     57% |67, 92, 101, 147, 226-350, 368-406, 417, 426-427, 435, 472, 478-507, 576-577, 582-643, 646-676, 746-772, 782-783, 786-789, 918-929, 939-951, 957-961, 977-978, 989-993, 1012, 1038, 1042, 1083-1084 |
| django/chat/tasks.py                                                  |       92 |       35 |     62% |27-60, 64-72, 137-138, 141-146 |
| django/chat/utils.py                                                  |      541 |       78 |     86% |103-105, 146, 158-159, 168-172, 186-187, 196, 245, 269, 271-272, 283, 285-301, 309-310, 317-318, 368-384, 417-419, 434-436, 499-500, 504-511, 519, 536-540, 887-888, 1000, 1013-1028, 1042-1049, 1061, 1092, 1094-1099, 1103 |
| django/chat/views.py                                                  |      480 |       98 |     80% |85-93, 109-111, 148, 178-180, 183-185, 208, 222-229, 235, 346-350, 382-463, 489-490, 524, 528, 591, 611, 655-656, 728-729, 739, 861-865, 874, 932-969, 979-980, 989-994, 1038-1052 |
| django/import\_timer.py                                               |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                  |       77 |        7 |     91% |28-33, 42, 56-61, 70, 109 |
| django/laws/loading\_utils.py                                         |      282 |       78 |     72% |60-75, 131-135, 153, 186-189, 248, 266, 268, 270, 289, 292, 294, 309-310, 312-313, 410-413, 423-441, 467-471, 483, 502, 554-555, 596-598, 692-810, 826, 833 |
| django/laws/loading\_views.py                                         |      106 |       17 |     84% |89-91, 168-171, 184, 248-258 |
| django/laws/management/commands/load\_laws\_xml.py                    |       97 |       55 |     43% |87-145, 157, 172, 174-175, 181-191 |
| django/laws/models.py                                                 |      191 |       33 |     83% |34, 77, 84-89, 111-115, 133-140, 148-153, 160, 187, 235-236, 289-297, 313 |
| django/laws/prompts.py                                                |        4 |        0 |    100% |           |
| django/laws/search\_history/models.py                                 |       20 |        3 |     85% |37, 42, 46 |
| django/laws/search\_history/views.py                                  |       51 |       37 |     27% |15-37, 43-92, 99-104 |
| django/laws/tasks.py                                                  |      317 |      113 |     64% |48-51, 62, 128, 141, 143, 150, 166-169, 211-215, 224-225, 234-235, 287-301, 313-338, 351, 369-380, 400-406, 455-468, 508-509, 516-533, 546, 550-552, 555-557, 563-578 |
| django/laws/test\_retriever\_performance.py                           |       60 |       10 |     83% |60-62, 81-83, 106-108, 117 |
| django/laws/translation.py                                            |        5 |        0 |    100% |           |
| django/laws/utils.py                                                  |       90 |       12 |     87% |24-26, 44, 90, 109-115, 132-136 |
| django/laws/views.py                                                  |      304 |      177 |     42% |81, 85, 98, 108, 116-117, 123-214, 226, 242, 280, 282, 287-289, 296-322, 336, 372, 380, 388, 397, 411-431, 438-446, 450-573, 580-654 |
| django/librarian/forms.py                                             |      101 |        4 |     96% |125-126, 211, 229 |
| django/librarian/models.py                                            |      337 |       47 |     86% |54-56, 124, 126, 134, 136, 138, 148, 173-175, 197, 251, 313-314, 319, 330-333, 408, 425-434, 438, 456, 490-492, 502-503, 509, 525, 552-553, 563-564, 574-575, 587-588 |
| django/librarian/tasks.py                                             |      117 |       44 |     62% |42-75, 82, 92, 105, 115, 123-125, 143-144, 147, 167-169, 180-183, 202-203 |
| django/librarian/translation.py                                       |        8 |        0 |    100% |           |
| django/librarian/utils/extract\_emails.py                             |      109 |       31 |     72% |58-72, 85, 87, 95-101, 119, 122, 131-143, 153, 155 |
| django/librarian/utils/extract\_zip.py                                |       68 |       12 |     82% |37-39, 50-59, 92 |
| django/librarian/utils/markdown\_splitter.py                          |      185 |       10 |     95% |72, 75-77, 88, 126, 140, 263, 273, 280 |
| django/librarian/utils/process\_document.py                           |       21 |        1 |     95% |        35 |
| django/librarian/utils/process\_engine.py                             |      547 |      107 |     80% |62-64, 89-98, 113, 184, 187, 193, 202-203, 207, 210, 213, 216, 223, 225, 227, 229, 231, 233, 237, 239, 241, 243, 278, 301, 303-305, 316, 318, 336-337, 353-364, 367-369, 386-412, 416-422, 432, 441-455, 500, 541-543, 589, 592-596, 602-606, 610, 658-659, 705, 839, 864, 875 |
| django/librarian/views.py                                             |      469 |      130 |     72% |37-41, 60-67, 123-144, 150, 166, 193-212, 227, 260, 322-323, 328, 362, 368, 386, 401-405, 434-435, 441-442, 460, 471-472, 475, 488, 492-496, 526, 533-535, 653, 658, 674-709, 746, 828-843, 847-892 |
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
| django/otto/settings.py                                               |      169 |       24 |     86% |46-49, 59-60, 223-232, 306, 319, 376-383, 415, 505-506, 556 |
| django/otto/tasks.py                                                  |       57 |       17 |     70% |11, 33, 53, 67, 72-75, 80-88, 96-98 |
| django/otto/templatetags/filters.py                                   |       10 |        1 |     90% |         8 |
| django/otto/templatetags/tags.py                                      |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                            |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                             |       37 |        9 |     76% |14-28, 65-67 |
| django/otto/utils/common.py                                           |       70 |        4 |     94% |102, 131-133 |
| django/otto/utils/decorators.py                                       |       64 |        4 |     94% |25-26, 67, 90 |
| django/otto/utils/logging.py                                          |       15 |        0 |    100% |           |
| django/otto/utils/middleware.py                                       |       41 |        1 |     98% |        31 |
| django/otto/views.py                                                  |      639 |      156 |     76% |60, 65-66, 71-85, 120, 134, 145-155, 168, 304-305, 406, 423, 472-475, 491-492, 517, 527-535, 566-576, 588-593, 596, 605, 607-610, 612-613, 615-618, 641, 649, 658, 674-685, 791-792, 823, 825, 827, 841, 843, 850-851, 854-857, 867-873, 883, 885, 887, 892-912, 951, 960-969, 1048, 1056-1062, 1085-1086, 1100-1103, 1114, 1130-1133, 1145, 1178-1201, 1205-1213, 1237-1242, 1256, 1259-1261, 1295-1297, 1317-1320 |
| django/postgres\_wrapper/base.py                                      |        6 |        0 |    100% |           |
| django/text\_extractor/models.py                                      |       18 |        1 |     94% |        29 |
| django/text\_extractor/tasks.py                                       |      104 |       61 |     41% |34-131, 163, 190, 198-214 |
| django/text\_extractor/utils.py                                       |      130 |       32 |     75% |58-81, 117-121, 171-172, 185-191 |
| django/text\_extractor/views.py                                       |      163 |       45 |     72% |49, 67-75, 83-86, 109-131, 144-165, 180, 184, 192-213, 218, 223-228, 251, 257-258, 280-281, 304-306, 314 |
|                                                             **TOTAL** | **8792** | **2184** | **75%** |           |


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