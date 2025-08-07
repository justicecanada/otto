# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                  |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/cache\_tiktoken.py                                             |        9 |        9 |      0% |      1-21 |
| django/chat/forms.py                                                  |      207 |       39 |     81% |46, 53, 140-155, 163-177, 196, 241, 249, 478, 480-482, 562-564, 584-608 |
| django/chat/llm.py                                                    |      186 |       53 |     72% |44, 71-84, 102, 124-141, 144-146, 159-160, 164-169, 185-202, 288-291, 294-297, 355, 377 |
| django/chat/llm\_models.py                                            |       76 |        8 |     89% |76, 81-83, 88, 288, 292, 302 |
| django/chat/models.py                                                 |      348 |       53 |     85% |38, 89, 150-154, 157-161, 284-287, 292-298, 306, 414-418, 422, 426-430, 436, 442, 448, 480, 500, 518-522, 574, 578-580, 595, 606, 644, 654, 672-675, 685-686 |
| django/chat/prompts.py                                                |        5 |        0 |    100% |           |
| django/chat/responses.py                                              |      374 |      123 |     67% |67, 92, 101, 147, 206, 208-223, 253, 262, 269, 306, 312-341, 410-411, 416-477, 480-510, 581-599, 606-607, 617-618, 754-765, 775-787, 793-797, 813-814, 825-829, 848, 874, 878, 919-920 |
| django/chat/tasks.py                                                  |       71 |       16 |     77% |22-30, 91-92, 95-100 |
| django/chat/utils.py                                                  |      525 |       66 |     87% |102-104, 145, 157-158, 174-178, 192-193, 202, 251, 275, 277-278, 289, 291-307, 315-316, 323-324, 374-395, 428-430, 445-447, 510-511, 515-522, 530, 547-551, 898-899, 1012-1013, 1023, 1054, 1056-1061, 1065 |
| django/chat/views.py                                                  |      488 |      103 |     79% |88-96, 112-114, 152, 182-184, 187-189, 213, 230-237, 243, 354-358, 390-471, 497-498, 532, 536, 599, 619, 663-664, 736-737, 747, 825-833, 874-878, 887, 945-982, 992-993, 1002-1007, 1051-1065 |
| django/import\_timer.py                                               |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                  |       77 |        7 |     91% |28-33, 42, 56-61, 70, 103 |
| django/laws/loading\_utils.py                                         |      282 |       78 |     72% |60-75, 131-135, 153, 186-189, 248, 266, 268, 270, 289, 292, 294, 309-310, 312-313, 410-413, 423-441, 467-471, 483, 502, 554-555, 596-598, 692-810, 826, 833 |
| django/laws/loading\_views.py                                         |      106 |       17 |     84% |89-91, 168-171, 184, 248-258 |
| django/laws/management/commands/load\_laws\_xml.py                    |       97 |       55 |     43% |87-145, 157, 172, 174-175, 181-191 |
| django/laws/models.py                                                 |      191 |       33 |     83% |34, 77, 84-89, 111-115, 133-140, 148-153, 160, 187, 235-236, 289-297, 313 |
| django/laws/prompts.py                                                |        4 |        0 |    100% |           |
| django/laws/search\_history/models.py                                 |       20 |        3 |     85% |37, 42, 46 |
| django/laws/search\_history/views.py                                  |       51 |       37 |     27% |15-37, 43-91, 98-103 |
| django/laws/tasks.py                                                  |      317 |      113 |     64% |48-51, 62, 128, 141, 143, 150, 166-169, 211-215, 224-225, 234-235, 287-301, 313-338, 351, 369-380, 400-406, 455-468, 508-509, 516-533, 546, 550-552, 555-557, 563-578 |
| django/laws/test\_retriever\_performance.py                           |       60 |       10 |     83% |60-62, 81-83, 106-108, 117 |
| django/laws/translation.py                                            |        5 |        0 |    100% |           |
| django/laws/utils.py                                                  |       84 |       12 |     86% |23-25, 43, 85, 104-110, 127-131 |
| django/laws/views.py                                                  |      301 |      176 |     42% |79, 83, 100, 108-109, 115-206, 218, 234, 272, 274, 279-281, 288-314, 328, 364, 372, 380, 389, 403-423, 430-438, 442-565, 572-646 |
| django/librarian/forms.py                                             |      101 |        4 |     96% |125-126, 211, 229 |
| django/librarian/models.py                                            |      332 |       47 |     86% |53-55, 123, 125, 133, 135, 137, 147, 172-174, 196, 250, 312-313, 318, 329-332, 407, 424-433, 437, 455, 483-485, 495-496, 502, 518, 545-546, 556-557, 567-568, 580-581 |
| django/librarian/tasks.py                                             |      116 |       41 |     65% |42-75, 82, 92, 105, 115, 138-139, 142, 164-166, 177-180, 199-200 |
| django/librarian/translation.py                                       |        8 |        0 |    100% |           |
| django/librarian/utils/extract\_emails.py                             |      109 |       31 |     72% |58-72, 85, 87, 95-101, 119, 122, 131-143, 153, 155 |
| django/librarian/utils/extract\_zip.py                                |       68 |       12 |     82% |37-39, 50-59, 92 |
| django/librarian/utils/markdown\_splitter.py                          |      185 |       10 |     95% |72, 75-77, 88, 126, 140, 263, 273, 280 |
| django/librarian/utils/process\_document.py                           |       21 |        1 |     95% |        35 |
| django/librarian/utils/process\_engine.py                             |      494 |       63 |     87% |52-54, 174, 177, 183, 192-193, 197, 203, 206, 213, 215, 217, 219, 221, 223, 229, 231, 233, 281, 294, 312-313, 326-335, 337-339, 385-399, 444, 468, 484-486, 535-539, 545-549, 553, 601-602, 636 |
| django/librarian/views.py                                             |      349 |       65 |     81% |82-103, 109, 137-156, 189, 251-252, 257, 293, 330-331, 358, 365-367, 485, 490, 506-541, 578 |
| django/otto/celery.py                                                 |       16 |        1 |     94% |        99 |
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
| django/otto/models.py                                                 |      295 |       30 |     90% |28-30, 89-92, 125, 129-132, 167, 213, 216, 232, 253, 271, 397, 400, 454, 461, 489, 493, 500, 506, 555-556, 570, 574, 578, 601 |
| django/otto/rules.py                                                  |      174 |       15 |     91% |28, 45, 52, 54, 116-118, 123-125, 153, 221-223, 270 |
| django/otto/secure\_models.py                                         |      248 |       94 |     62% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 429-434, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                               |      166 |       24 |     86% |46-49, 59-60, 223-232, 306, 319, 376-383, 415, 505-506, 550 |
| django/otto/tasks.py                                                  |       57 |       17 |     70% |11, 33, 53, 67, 72-75, 80-88, 96-98 |
| django/otto/templatetags/filters.py                                   |       10 |        1 |     90% |         8 |
| django/otto/templatetags/tags.py                                      |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                            |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                             |       37 |        9 |     76% |14-28, 66-68 |
| django/otto/utils/common.py                                           |       71 |        4 |     94% |101, 130-132 |
| django/otto/utils/decorators.py                                       |       64 |        4 |     94% |25-26, 67, 90 |
| django/otto/utils/logging.py                                          |       15 |        0 |    100% |           |
| django/otto/utils/middleware.py                                       |       41 |        1 |     98% |        31 |
| django/otto/views.py                                                  |      595 |      139 |     77% |60, 65, 70-84, 128, 136, 147-157, 170, 306-307, 408, 425, 474-477, 493-494, 519, 529-537, 568-578, 590-595, 598, 607, 609-612, 614-615, 617-620, 643, 651, 660, 676-687, 793-794, 825, 827, 829, 843, 845, 852-853, 856-859, 869-875, 885, 887, 889, 894-914, 953, 962-971, 1050, 1058-1064, 1087-1088, 1108, 1124-1127, 1139, 1172-1195, 1219-1224, 1232-1235 |
| django/postgres\_wrapper/base.py                                      |        6 |        0 |    100% |           |
| django/text\_extractor/models.py                                      |       18 |        1 |     94% |        29 |
| django/text\_extractor/tasks.py                                       |       28 |        7 |     75% |     50-61 |
| django/text\_extractor/utils.py                                       |      280 |       73 |     74% |62-85, 121-125, 148, 188-192, 223-241, 254-256, 267-272, 303-308, 413-415, 426-431, 472-480, 506-514, 531-532, 538, 544-548 |
| django/text\_extractor/views.py                                       |      119 |       28 |     76% |47, 66-81, 93, 107-115, 128-149, 167-169, 174, 179-184, 201, 211-212, 233-234 |
|                                                             **TOTAL** | **8352** | **1812** | **78%** |           |


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