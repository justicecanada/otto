# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                  |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/cache\_tiktoken.py                                             |        8 |        8 |      0% |      1-18 |
| django/chat/forms.py                                                  |      188 |       40 |     79% |42, 49, 106, 139-154, 162-176, 195, 240, 248, 414, 416-418, 498-500, 520-544 |
| django/chat/llm.py                                                    |      116 |       20 |     83% |81, 103-105, 111-113, 139-156, 263, 285 |
| django/chat/models.py                                                 |      325 |       43 |     87% |33, 84, 220-223, 228-234, 242, 350-354, 358, 362-366, 372, 378, 384, 415, 435, 453-457, 509, 513-515, 530, 541, 579, 589, 607-610, 620-621 |
| django/chat/prompts.py                                                |        5 |        0 |    100% |           |
| django/chat/responses.py                                              |      340 |      111 |     67% |70, 95, 104, 167, 226, 228-243, 273, 282, 289, 326, 332-361, 430-431, 436-497, 500-530, 572, 578-588, 638, 684-718, 724-728, 787, 813, 817, 858-859 |
| django/chat/tasks.py                                                  |       71 |       16 |     77% |22-30, 91-92, 95-100 |
| django/chat/utils.py                                                  |      493 |       64 |     87% |92-94, 135, 147-148, 164-168, 182-183, 190, 239, 263, 265-266, 277, 279-295, 303-304, 311-312, 356-377, 410-412, 427-429, 491-498, 506, 523-527, 563-573, 580, 874-875, 988-989, 999 |
| django/chat/views.py                                                  |      452 |       99 |     78% |85-93, 109-111, 149, 177-179, 182-184, 208, 225-232, 238, 349-353, 385-466, 492-493, 526, 529, 592, 605, 640-641, 710-718, 751-755, 764, 820-857, 867-868, 877-880, 924-938 |
| django/import\_timer.py                                               |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                  |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                    |      451 |      120 |     73% |28-57, 72, 83-85, 101-104, 114-118, 146, 175, 236, 254, 256, 258, 277, 280, 282, 297-298, 300-301, 398-401, 411-429, 455-459, 471, 497, 549-550, 591-593, 709-715, 733-734, 736, 744, 784, 786, 804-806, 845-847, 850-852, 879-881, 883-885, 887-889, 891-893, 946-948, 965-967, 985-991, 1039-1050, 1055, 1068-1069, 1094-1100 |
| django/laws/models.py                                                 |      105 |       22 |     79% |38-42, 86, 111-114, 148, 152-160, 164-165 |
| django/laws/prompts.py                                                |        2 |        0 |    100% |           |
| django/laws/translation.py                                            |        5 |        0 |    100% |           |
| django/laws/utils.py                                                  |       76 |       12 |     84% |41, 66-71, 82, 98-104 |
| django/laws/views.py                                                  |      217 |       30 |     86% |71, 75, 92, 105, 125, 155-162, 172, 207, 224, 246, 289, 291, 296-298, 310, 314, 340, 348, 356, 365, 369, 376-381, 444-458 |
| django/librarian/forms.py                                             |      101 |        5 |     95% |125-126, 211, 215, 229 |
| django/librarian/models.py                                            |      332 |       47 |     86% |53-55, 123, 125, 133, 135, 137, 147, 172-174, 196, 250, 312-313, 318, 329-332, 407, 424-433, 437, 455, 483-485, 495-496, 502, 518, 545-546, 556-557, 567-568, 580-581 |
| django/librarian/tasks.py                                             |      116 |       41 |     65% |42-75, 82, 92, 105, 115, 138-139, 142, 164-166, 177-180, 199-200 |
| django/librarian/translation.py                                       |        8 |        0 |    100% |           |
| django/librarian/utils/extract\_emails.py                             |      109 |       24 |     78% |84, 86, 94-100, 118, 121, 130-142, 152, 154 |
| django/librarian/utils/extract\_zip.py                                |       68 |       12 |     82% |37-39, 50-59, 92 |
| django/librarian/utils/markdown\_splitter.py                          |      185 |       10 |     95% |72, 75-77, 88, 126, 140, 263, 273, 280 |
| django/librarian/utils/process\_document.py                           |       21 |        1 |     95% |        35 |
| django/librarian/utils/process\_engine.py                             |      493 |       63 |     87% |47-49, 169, 172, 178, 187-188, 192, 198, 201, 208, 210, 212, 214, 216, 218, 224, 226, 228, 276, 289, 307-308, 321-330, 332-334, 380-394, 439, 463, 479-481, 530-534, 540-544, 548, 596-597, 631 |
| django/librarian/views.py                                             |      343 |       65 |     81% |73-94, 100, 128-147, 180, 240-241, 246, 282, 315-316, 343, 350-352, 470, 475, 491-526, 563 |
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
| django/otto/views.py                                                  |      588 |      134 |     77% |59, 64, 69-83, 127, 135, 146-156, 168, 293, 393, 410, 459-462, 478-479, 504, 514-522, 553-563, 575-580, 583, 592, 594-597, 599-600, 602-605, 628, 636, 645, 661-672, 778-779, 810, 812, 814, 828, 830, 837-838, 841-844, 854-860, 870, 872, 874, 879-899, 938, 947-956, 1035, 1043-1049, 1072-1073, 1093, 1124, 1157-1180, 1204-1209, 1217-1220 |
| django/postgres\_wrapper/base.py                                      |        6 |        0 |    100% |           |
| django/text\_extractor/models.py                                      |       18 |        1 |     94% |        29 |
| django/text\_extractor/tasks.py                                       |       28 |        7 |     75% |     50-61 |
| django/text\_extractor/utils.py                                       |      254 |       69 |     73% |61-84, 119-120, 154-158, 180-198, 211-213, 223-228, 256-261, 365-367, 378-383, 422-430, 456-464, 481-482, 488, 494-498 |
| django/text\_extractor/views.py                                       |      117 |       27 |     77% |47, 65-80, 90, 104-112, 125-146, 164-166, 171, 176-181, 198, 208, 229-230 |
|                                                             **TOTAL** | **7304** | **1370** | **81%** |           |


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