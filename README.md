# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                  |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/cache\_tiktoken.py                                             |        8 |        8 |      0% |      1-18 |
| django/chat/forms.py                                                  |      163 |       21 |     87% |43, 50, 107, 140-155, 163-177, 196, 241, 249, 420, 422-424, 504-506 |
| django/chat/llm.py                                                    |      112 |       18 |     84% |78, 100-102, 108-110, 136-151, 256, 278 |
| django/chat/models.py                                                 |      342 |       38 |     89% |33, 84, 222-225, 230-236, 244, 364, 381-382, 386-390, 397, 402, 408-409, 412, 441, 461, 479-483, 535, 539-541, 556, 567, 605, 615, 646-647 |
| django/chat/prompts.py                                                |        5 |        0 |    100% |           |
| django/chat/responses.py                                              |      308 |       87 |     72% |74, 116, 214, 268, 274-294, 362-363, 368-401, 404-434, 476, 482-492, 542, 588-622, 628-632, 691, 718, 722, 763-764 |
| django/chat/tasks.py                                                  |       71 |       16 |     77% |22-30, 92-93, 96-101 |
| django/chat/templatetags/chat\_tags.py                                |        5 |        0 |    100% |           |
| django/chat/utils.py                                                  |      422 |       59 |     86% |126, 138-139, 151-155, 199, 218, 220-221, 233, 235-251, 259-260, 267-268, 307-323, 355-357, 372-374, 396, 468, 470, 487, 541-548, 556, 573-577, 613-623, 630, 924-925 |
| django/chat/views.py                                                  |      458 |       81 |     82% |83-91, 107-109, 147, 175-177, 180-182, 206, 223-230, 236, 337-341, 427, 447-467, 493-495, 525, 528, 594, 607, 642-643, 712-720, 752-764, 827-843, 853-854, 863-866, 904-913, 919-924 |
| django/import\_timer.py                                               |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                  |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                    |      451 |      120 |     73% |28-57, 72, 83-85, 101-104, 114-118, 146, 175, 236, 254, 256, 258, 277, 280, 282, 297-298, 300-301, 398-401, 411-429, 455-459, 471, 497, 549-550, 591-593, 709-715, 733-734, 736, 744, 784, 786, 804-806, 845-847, 850-852, 879-881, 883-885, 887-889, 891-893, 946-948, 965-967, 985-991, 1039-1050, 1055, 1068-1069, 1094-1100 |
| django/laws/models.py                                                 |      105 |       22 |     79% |38-42, 86, 111-114, 148, 152-160, 164-165 |
| django/laws/prompts.py                                                |        2 |        0 |    100% |           |
| django/laws/translation.py                                            |        5 |        0 |    100% |           |
| django/laws/utils.py                                                  |       71 |       11 |     85% |37, 62-67, 78, 94-96 |
| django/laws/views.py                                                  |      216 |       29 |     87% |71, 75, 92, 105, 122, 152-159, 169, 204, 221, 243, 286, 288, 293-295, 307, 311, 337, 345, 353, 362, 366, 373-378, 441-449 |
| django/librarian/forms.py                                             |      101 |        5 |     95% |125-126, 211, 215, 229 |
| django/librarian/models.py                                            |      330 |       48 |     85% |53-55, 123, 125, 133, 135, 137, 147, 172-174, 192, 196, 250, 312-313, 318, 329-332, 405, 422-431, 435, 453, 481-483, 493-494, 500, 516, 542-543, 553-554, 564-565, 578-579 |
| django/librarian/tasks.py                                             |      113 |       39 |     65% |42-75, 82, 92, 105, 115, 135, 157-159, 170-173, 192-193 |
| django/librarian/translation.py                                       |        8 |        0 |    100% |           |
| django/librarian/utils/markdown\_splitter.py                          |      183 |       10 |     95% |72, 75-77, 88, 123, 137, 260, 270, 277 |
| django/librarian/utils/process\_engine.py                             |      464 |       55 |     88% |45-47, 52, 148, 153, 163-164, 168, 174, 177, 184, 186, 188, 190, 196, 198, 200, 248, 261, 273-274, 287-296, 298-300, 353-359, 405, 429, 445-447, 496-500, 506-510, 514, 562-563, 597 |
| django/librarian/views.py                                             |      309 |       42 |     86% |71-92, 98, 126-145, 178, 238-239, 244, 280, 312-313, 332, 339-341, 459, 464 |
| django/otto/celery.py                                                 |       16 |        1 |     94% |        78 |
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
| django/otto/rules.py                                                  |      165 |       18 |     89% |28, 44, 51, 53, 115-117, 122-124, 129-131, 154, 220-222, 258 |
| django/otto/secure\_models.py                                         |      248 |       94 |     62% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 429-434, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                               |      158 |       23 |     85% |38-41, 51-52, 214-223, 293, 306, 363-370, 402, 492-493 |
| django/otto/tasks.py                                                  |       42 |        7 |     83% |10, 15, 39, 59, 72-74 |
| django/otto/templatetags/filters.py                                   |       10 |        1 |     90% |         8 |
| django/otto/templatetags/tags.py                                      |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                            |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                             |       37 |        9 |     76% |14-28, 66-68 |
| django/otto/utils/common.py                                           |       62 |        1 |     98% |        98 |
| django/otto/utils/decorators.py                                       |       62 |        4 |     94% |24-25, 65, 87 |
| django/otto/utils/logging.py                                          |       15 |        0 |    100% |           |
| django/otto/utils/middleware.py                                       |       41 |        1 |     98% |        31 |
| django/otto/views.py                                                  |      570 |      130 |     77% |58, 63, 68-82, 124, 133-143, 155, 280, 380, 432-435, 451-452, 476, 486-489, 518-528, 540-545, 548, 557, 559-562, 564-565, 567-570, 593, 601, 610, 626-637, 743-744, 775, 777, 779, 793, 795, 802-803, 806-809, 819-825, 835, 837, 839, 844-864, 903, 912-921, 1000, 1007-1013, 1036-1037, 1057, 1088, 1121-1144, 1168-1173, 1181-1184 |
| django/postgres\_wrapper/base.py                                      |        6 |        0 |    100% |           |
| django/text\_extractor/models.py                                      |       17 |        1 |     94% |        28 |
| django/text\_extractor/tasks.py                                       |       18 |        2 |     89% |     34-35 |
| django/text\_extractor/utils.py                                       |      211 |       42 |     80% |57-80, 115-116, 164-166, 184, 295-297, 351-355, 362-363, 369, 375-379 |
| django/text\_extractor/views.py                                       |      108 |       21 |     81% |41, 59-74, 84, 98-106, 119-125, 142, 146, 163, 173, 193-194 |
|                                                             **TOTAL** | **6827** | **1179** | **83%** |           |


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