# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                  |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/cache\_tiktoken.py                                             |        8 |        8 |      0% |      1-18 |
| django/chat/forms.py                                                  |      184 |       36 |     80% |46, 53, 110, 143-158, 166-180, 199, 244, 252, 423, 425-427, 507-509, 529-547 |
| django/chat/llm.py                                                    |      114 |       19 |     83% |79, 101-103, 109-111, 137-153, 258, 280 |
| django/chat/models.py                                                 |      331 |       38 |     89% |33, 84, 224-227, 232-238, 246, 358, 362-366, 372, 378, 384, 415, 435, 453-457, 509, 513-515, 530, 541, 579, 589, 607-610, 620-621 |
| django/chat/prompts.py                                                |        5 |        0 |    100% |           |
| django/chat/responses.py                                              |      335 |      101 |     70% |71, 96, 105, 168, 231, 233-249, 283, 292, 299, 340, 346-366, 434-435, 440-477, 480-510, 552, 558-568, 618, 664-698, 704-708, 767, 794, 798, 839-840 |
| django/chat/tasks.py                                                  |       71 |       16 |     77% |22-30, 91-92, 95-100 |
| django/chat/utils.py                                                  |      488 |       64 |     87% |91-93, 133, 145-146, 158-162, 211, 235, 237-238, 249, 251-267, 275-276, 283-284, 328-344, 377-379, 394-396, 418, 490, 492, 509, 563-570, 578, 595-599, 635-645, 652, 946-947, 1060-1061, 1071 |
| django/chat/views.py                                                  |      434 |       98 |     77% |83-91, 107-109, 147, 175-177, 180-182, 206, 223-230, 236, 340-344, 376-436, 462-464, 494, 497, 563, 576, 611-612, 681-689, 721-733, 780-817, 827-828, 837-840, 884-889 |
| django/import\_timer.py                                               |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                  |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                    |      451 |      120 |     73% |28-57, 72, 83-85, 101-104, 114-118, 146, 175, 236, 254, 256, 258, 277, 280, 282, 297-298, 300-301, 398-401, 411-429, 455-459, 471, 497, 549-550, 591-593, 709-715, 733-734, 736, 744, 784, 786, 804-806, 845-847, 850-852, 879-881, 883-885, 887-889, 891-893, 946-948, 965-967, 985-991, 1039-1050, 1055, 1068-1069, 1094-1100 |
| django/laws/models.py                                                 |      105 |       22 |     79% |38-42, 86, 111-114, 148, 152-160, 164-165 |
| django/laws/prompts.py                                                |        2 |        0 |    100% |           |
| django/laws/translation.py                                            |        5 |        0 |    100% |           |
| django/laws/utils.py                                                  |       71 |       11 |     85% |37, 62-67, 78, 94-96 |
| django/laws/views.py                                                  |      216 |       29 |     87% |71, 75, 92, 105, 122, 152-159, 169, 204, 221, 243, 286, 288, 293-295, 307, 311, 337, 345, 353, 362, 366, 373-378, 441-449 |
| django/librarian/forms.py                                             |      101 |        5 |     95% |125-126, 211, 215, 229 |
| django/librarian/models.py                                            |      331 |       47 |     86% |53-55, 123, 125, 133, 135, 137, 147, 172-174, 196, 250, 312-313, 318, 329-332, 407, 424-433, 437, 455, 483-485, 495-496, 502, 518, 544-545, 555-556, 566-567, 579-580 |
| django/librarian/tasks.py                                             |      116 |       41 |     65% |42-75, 82, 92, 105, 115, 138-139, 142, 164-166, 177-180, 199-200 |
| django/librarian/translation.py                                       |        8 |        0 |    100% |           |
| django/librarian/utils/extract\_emails.py                             |       65 |        9 |     86% |81, 83, 91-97 |
| django/librarian/utils/extract\_zip.py                                |       68 |       12 |     82% |37-39, 50-59, 92 |
| django/librarian/utils/markdown\_splitter.py                          |      185 |       10 |     95% |72, 75-77, 88, 126, 140, 263, 273, 280 |
| django/librarian/utils/process\_document.py                           |       21 |        3 |     86% | 19-20, 35 |
| django/librarian/utils/process\_engine.py                             |      475 |       62 |     87% |47-49, 54, 158, 161, 170-171, 175, 181, 184, 191, 193, 195, 197, 199, 205, 207, 209, 257, 270, 286-287, 300-309, 311-313, 359-373, 418, 442, 458-460, 509-513, 519-523, 527, 575-576, 610 |
| django/librarian/views.py                                             |      317 |       43 |     86% |72-93, 99, 127-146, 179, 239-240, 245, 281, 313-314, 341, 348-350, 468, 473, 514 |
| django/otto/celery.py                                                 |       16 |        1 |     94% |        88 |
| django/otto/context\_processors.py                                    |       11 |        4 |     64% |     10-14 |
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
| django/otto/models.py                                                 |      290 |       30 |     90% |28-30, 89-92, 125, 129-132, 167, 213, 216, 232, 253, 271, 388, 391, 445, 452, 480, 484, 491, 497, 546-547, 561, 565, 569, 591 |
| django/otto/rules.py                                                  |      159 |       15 |     91% |28, 44, 51, 53, 115-117, 122-124, 146, 212-214, 250 |
| django/otto/secure\_models.py                                         |      248 |       94 |     62% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 429-434, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                               |      164 |       24 |     85% |38-41, 51-52, 215-224, 294, 307, 364-371, 403, 493-494, 538 |
| django/otto/tasks.py                                                  |       50 |       11 |     78% |10, 15, 39, 59, 73, 78-81, 89-91 |
| django/otto/templatetags/filters.py                                   |       10 |        1 |     90% |         8 |
| django/otto/templatetags/tags.py                                      |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                            |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                             |       37 |        9 |     76% |14-28, 66-68 |
| django/otto/utils/common.py                                           |       71 |        4 |     94% |101, 130-132 |
| django/otto/utils/decorators.py                                       |       63 |        4 |     94% |25-26, 66, 88 |
| django/otto/utils/logging.py                                          |       15 |        0 |    100% |           |
| django/otto/utils/middleware.py                                       |       41 |        1 |     98% |        31 |
| django/otto/views.py                                                  |      584 |      132 |     77% |58, 63, 68-82, 126, 134, 145-155, 167, 292, 392, 409, 458-461, 477-478, 502, 512-515, 544-554, 566-571, 574, 583, 585-588, 590-591, 593-596, 619, 627, 636, 652-663, 769-770, 801, 803, 805, 819, 821, 828-829, 832-835, 845-851, 861, 863, 865, 870-890, 929, 938-947, 1026, 1033-1039, 1062-1063, 1083, 1114, 1147-1170, 1194-1199, 1207-1210 |
| django/postgres\_wrapper/base.py                                      |        6 |        0 |    100% |           |
| django/text\_extractor/models.py                                      |       17 |        1 |     94% |        28 |
| django/text\_extractor/tasks.py                                       |       18 |        2 |     89% |     34-35 |
| django/text\_extractor/utils.py                                       |      211 |       42 |     80% |57-80, 115-116, 164-166, 184, 295-297, 351-355, 362-363, 369, 375-379 |
| django/text\_extractor/views.py                                       |      108 |       21 |     81% |45, 63-78, 88, 102-110, 123-129, 146, 150, 167, 177, 198-199 |
|                                                             **TOTAL** | **7114** | **1271** | **82%** |           |


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