# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                                                          |    Stmts |     Miss |   Cover |   Missing |
|-------------------------------------------------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/chat/\_\_init\_\_.py                                                                                   |        0 |        0 |    100% |           |
| django/chat/admin.py                                                                                          |        1 |        1 |      0% |         1 |
| django/chat/apps.py                                                                                           |        4 |        0 |    100% |           |
| django/chat/forms.py                                                                                          |      145 |       23 |     84% |40, 48, 74, 94-106, 110-115, 124, 139, 143-148, 157, 328, 330-332 |
| django/chat/llm.py                                                                                            |       99 |       13 |     87% |71, 90-92, 98-100, 118-124, 244 |
| django/chat/metrics/\_\_init\_\_.py                                                                           |        0 |        0 |    100% |           |
| django/chat/metrics/activity\_metrics.py                                                                      |        4 |        0 |    100% |           |
| django/chat/metrics/feedback\_metrics.py                                                                      |        3 |        0 |    100% |           |
| django/chat/migrations/0001\_initial.py                                                                       |        6 |        0 |    100% |           |
| django/chat/migrations/0002\_initial.py                                                                       |        6 |        0 |    100% |           |
| django/chat/migrations/0003\_initial.py                                                                       |        7 |        0 |    100% |           |
| django/chat/migrations/0004\_remove\_answersource\_saved\_data\_source\_name.py                               |        4 |        0 |    100% |           |
| django/chat/migrations/0005\_alter\_chatoptions\_chat\_system\_prompt.py                                      |        4 |        0 |    100% |           |
| django/chat/migrations/0006\_alter\_chatoptions\_chat\_model\_and\_more.py                                    |        4 |        0 |    100% |           |
| django/chat/migrations/0007\_chatoptions\_qa\_prompt.py                                                       |        4 |        0 |    100% |           |
| django/chat/migrations/0008\_remove\_chatoptions\_qa\_prompt\_and\_more.py                                    |        4 |        0 |    100% |           |
| django/chat/migrations/0009\_chatoptions\_qa\_prompt\_template.py                                             |        4 |        0 |    100% |           |
| django/chat/migrations/0010\_chatoptions\_qa\_answer\_mode\_chatoptions\_qa\_prune\_and\_more.py              |        4 |        0 |    100% |           |
| django/chat/migrations/0011\_chatoptions\_qa\_rewrite.py                                                      |        4 |        0 |    100% |           |
| django/chat/migrations/0012\_alter\_message\_cost.py                                                          |        4 |        0 |    100% |           |
| django/chat/migrations/0013\_rename\_cost\_message\_usd\_cost.py                                              |        4 |        0 |    100% |           |
| django/chat/migrations/0014\_chatoptions\_qa\_documents\_chatoptions\_qa\_scope.py                            |        4 |        0 |    100% |           |
| django/chat/migrations/0015\_chatoptions\_chat\_agent\_alter\_chatoptions\_mode.py                            |        4 |        0 |    100% |           |
| django/chat/migrations/0016\_chat\_data\_source.py                                                            |        5 |        0 |    100% |           |
| django/chat/migrations/0017\_chatoptions\_qa\_mode\_alter\_chatoptions\_qa\_scope.py                          |        4 |        0 |    100% |           |
| django/chat/migrations/0018\_alter\_chatoptions\_qa\_source\_order.py                                         |        4 |        0 |    100% |           |
| django/chat/migrations/0019\_alter\_message\_parent.py                                                        |        5 |        0 |    100% |           |
| django/chat/migrations/0019\_chatoptions\_qa\_granularity.py                                                  |        4 |        0 |    100% |           |
| django/chat/migrations/0019\_remove\_chatoptions\_preset\_name\_and\_more.py                                  |        6 |        0 |    100% |           |
| django/chat/migrations/0020\_answersource\_group\_number.py                                                   |        4 |        0 |    100% |           |
| django/chat/migrations/0021\_merge\_20241008\_1956.py                                                         |        4 |        0 |    100% |           |
| django/chat/migrations/0022\_merge\_20241010\_1557.py                                                         |        4 |        0 |    100% |           |
| django/chat/migrations/0022\_remove\_chat\_data\_source\_remove\_chat\_options\_and\_more.py                  |        5 |        0 |    100% |           |
| django/chat/migrations/0023\_answersource\_max\_page\_answersource\_min\_page.py                              |        4 |        0 |    100% |           |
| django/chat/migrations/0023\_merge\_20241011\_1541.py                                                         |        4 |        0 |    100% |           |
| django/chat/migrations/0024\_remove\_preset\_editable\_by\_preset\_sharing\_option.py                         |        4 |        0 |    100% |           |
| django/chat/migrations/0025\_alter\_preset\_sharing\_option.py                                                |        4 |        0 |    100% |           |
| django/chat/migrations/0026\_alter\_preset\_sharing\_option.py                                                |        4 |        0 |    100% |           |
| django/chat/migrations/0027\_merge\_20241025\_2046.py                                                         |        4 |        0 |    100% |           |
| django/chat/migrations/0028\_alter\_preset\_sharing\_option.py                                                |        4 |        0 |    100% |           |
| django/chat/migrations/0029\_remove\_preset\_is\_public.py                                                    |        4 |        0 |    100% |           |
| django/chat/migrations/0030\_alter\_preset\_sharing\_option.py                                                |        4 |        0 |    100% |           |
| django/chat/migrations/0031\_alter\_preset\_sharing\_option.py                                                |        4 |        0 |    100% |           |
| django/chat/migrations/0032\_chatoptions\_prompt.py                                                           |        4 |        0 |    100% |           |
| django/chat/migrations/0033\_alter\_chatoptions\_prompt.py                                                    |        4 |        0 |    100% |           |
| django/chat/migrations/0033\_alter\_message\_usd\_cost.py                                                     |        4 |        0 |    100% |           |
| django/chat/migrations/0034\_chatoptions\_summarize\_gender\_neutral.py                                       |        4 |        0 |    100% |           |
| django/chat/migrations/0034\_merge\_20241112\_2042.py                                                         |        4 |        0 |    100% |           |
| django/chat/migrations/0035\_chatoptions\_summarize\_instructions.py                                          |        4 |        0 |    100% |           |
| django/chat/migrations/0036\_merge\_20241113\_2222.py                                                         |        4 |        0 |    100% |           |
| django/chat/migrations/0037\_remove\_answersource\_node\_text\_answersource\_node\_id.py                      |        4 |        0 |    100% |           |
| django/chat/migrations/0038\_alter\_chatoptions\_qa\_mode.py                                                  |        4 |        0 |    100% |           |
| django/chat/migrations/0039\_alter\_chatoptions\_qa\_mode.py                                                  |        4 |        0 |    100% |           |
| django/chat/migrations/0040\_alter\_chatfile\_filename.py                                                     |        4 |        0 |    100% |           |
| django/chat/migrations/0041\_chat\_loaded\_preset.py                                                          |        5 |        0 |    100% |           |
| django/chat/migrations/\_\_init\_\_.py                                                                        |        0 |        0 |    100% |           |
| django/chat/models.py                                                                                         |      288 |       33 |     89% |36, 85, 236-239, 244-250, 323, 337-338, 343-344, 348-352, 359, 364, 370-371, 374, 402, 418, 480, 484-486, 509, 547, 557 |
| django/chat/prompts.py                                                                                        |       10 |        0 |    100% |           |
| django/chat/responses.py                                                                                      |      280 |       75 |     73% |68, 106, 191, 245, 251-271, 337-338, 343-359, 362-392, 432, 438-448, 485, 531-565, 571-575, 621, 648, 652, 693-694 |
| django/chat/tasks.py                                                                                          |       71 |       16 |     77% |22-30, 92-93, 96-101 |
| django/chat/templatetags/\_\_init\_\_.py                                                                      |        0 |        0 |    100% |           |
| django/chat/templatetags/chat\_tags.py                                                                        |        5 |        0 |    100% |           |
| django/chat/urls.py                                                                                           |        6 |        0 |    100% |           |
| django/chat/utils.py                                                                                          |      276 |       52 |     81% |116, 128-129, 141-145, 189, 208, 210-211, 223-239, 246-247, 283-286, 292-299, 330-332, 347-349, 371, 443, 445, 462, 510-517, 525, 542-546, 552-562, 569 |
| django/chat/views.py                                                                                          |      433 |       69 |     84% |92-94, 127, 152-154, 157-159, 183, 203-210, 216, 256, 264, 295, 304-308, 405, 425-445, 476-481, 533, 536, 590, 634, 658, 693-694, 747-755, 819-821, 827-841, 851-853, 862-865, 900-909, 913-915 |
| django/import\_timer.py                                                                                       |        6 |        6 |      0% |       1-8 |
| django/laws/\_\_init\_\_.py                                                                                   |        0 |        0 |    100% |           |
| django/laws/admin.py                                                                                          |        1 |        1 |      0% |         1 |
| django/laws/apps.py                                                                                           |        4 |        0 |    100% |           |
| django/laws/forms.py                                                                                          |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                                                            |      442 |      121 |     73% |29, 33-62, 77, 88-90, 106-109, 119-123, 151, 180, 241, 259, 261, 263, 282, 285, 287, 302-303, 305-306, 403-406, 416-434, 460-464, 476, 502, 554-555, 596-598, 714-720, 738-739, 741, 749, 789, 791, 809-811, 841-843, 846-848, 856-858, 860-862, 864-866, 868-870, 923-925, 941-943, 961-967, 1015-1026, 1031, 1040-1041, 1066-1072 |
| django/laws/migrations/0001\_initial.py                                                                       |        5 |        0 |    100% |           |
| django/laws/migrations/0002\_remove\_law\_lang\_remove\_law\_law\_id\_and\_more.py                            |        4 |        0 |    100% |           |
| django/laws/migrations/\_\_init\_\_.py                                                                        |        0 |        0 |    100% |           |
| django/laws/models.py                                                                                         |      104 |       22 |     79% |42-46, 90, 115-118, 152, 156-164, 168-169 |
| django/laws/prompts.py                                                                                        |        2 |        0 |    100% |           |
| django/laws/translation.py                                                                                    |        5 |        0 |    100% |           |
| django/laws/urls.py                                                                                           |        4 |        0 |    100% |           |
| django/laws/utils.py                                                                                          |       70 |       21 |     70% |26-36, 41-47, 59-66, 77, 93-95 |
| django/laws/views.py                                                                                          |      210 |       44 |     79% |64-94, 105, 122, 152-159, 169, 204, 221, 243, 284, 286, 291-293, 305, 309, 335, 343, 351, 361, 368, 427-435 |
| django/librarian/\_\_init\_\_.py                                                                              |        0 |        0 |    100% |           |
| django/librarian/admin.py                                                                                     |        5 |        5 |      0% |       1-7 |
| django/librarian/apps.py                                                                                      |        4 |        0 |    100% |           |
| django/librarian/forms.py                                                                                     |       85 |       17 |     80% |187-198, 204-213 |
| django/librarian/metrics/\_\_init\_\_.py                                                                      |        0 |        0 |    100% |           |
| django/librarian/metrics/activity\_metrics.py                                                                 |        9 |        9 |      0% |      1-50 |
| django/librarian/migrations/0001\_initial.py                                                                  |        6 |        0 |    100% |           |
| django/librarian/migrations/0002\_initial.py                                                                  |        7 |        0 |    100% |           |
| django/librarian/migrations/0003\_library\_is\_default\_library.py                                            |        4 |        0 |    100% |           |
| django/librarian/migrations/0004\_document\_cost.py                                                           |        4 |        0 |    100% |           |
| django/librarian/migrations/0005\_rename\_cost\_document\_usd\_cost.py                                        |        4 |        0 |    100% |           |
| django/librarian/migrations/0006\_alter\_library\_options\_library\_is\_personal\_library.py                  |        4 |        0 |    100% |           |
| django/librarian/migrations/0007\_remove\_library\_chat\_datasource\_chat.py                                  |        5 |        0 |    100% |           |
| django/librarian/migrations/0008\_remove\_datasource\_chat.py                                                 |        4 |        0 |    100% |           |
| django/librarian/migrations/0009\_datasource\_chat.py                                                         |        5 |        0 |    100% |           |
| django/librarian/migrations/0010\_document\_pdf\_extraction\_method.py                                        |        4 |        0 |    100% |           |
| django/librarian/migrations/0011\_alter\_datasource\_options\_alter\_library\_options.py                      |        4 |        0 |    100% |           |
| django/librarian/migrations/0012\_remove\_document\_sha256\_hash.py                                           |        4 |        0 |    100% |           |
| django/librarian/migrations/0013\_document\_status\_details.py                                                |        4 |        0 |    100% |           |
| django/librarian/migrations/0014\_alter\_libraryuserrole\_user.py                                             |        6 |        0 |    100% |           |
| django/librarian/migrations/0015\_alter\_document\_extracted\_title\_and\_more.py                             |        4 |        0 |    100% |           |
| django/librarian/migrations/\_\_init\_\_.py                                                                   |        0 |        0 |    100% |           |
| django/librarian/models.py                                                                                    |      288 |       37 |     87% |53-55, 123, 125, 133, 135, 137, 143, 152-153, 164-166, 184, 188, 230, 292-293, 383-392, 407, 435-437, 447-448, 454, 470 |
| django/librarian/tasks.py                                                                                     |      113 |       39 |     65% |42-75, 82, 92, 105, 115, 135, 157-159, 170-173, 192-193 |
| django/librarian/translation.py                                                                               |        8 |        0 |    100% |           |
| django/librarian/urls.py                                                                                      |        4 |        0 |    100% |           |
| django/librarian/utils/markdown\_splitter.py                                                                  |      183 |       10 |     95% |72, 75-77, 88, 123, 137, 260, 270, 277 |
| django/librarian/utils/process\_engine.py                                                                     |      422 |       54 |     87% |44-46, 51, 145, 150, 160-161, 165, 171, 174, 181, 183, 185, 187, 193, 195, 197, 221, 234, 246-247, 260-269, 315-321, 358, 382, 398-400, 449-453, 459-463, 467, 515-516, 550, 637, 659 |
| django/librarian/views.py                                                                                     |      286 |       52 |     82% |69-89, 92, 122-144, 180-200, 229-244, 303-304, 323, 330-332, 450, 455 |
| django/manage.py                                                                                              |       11 |       11 |      0% |      3-23 |
| django/otto/\_\_init\_\_.py                                                                                   |        2 |        0 |    100% |           |
| django/otto/admin.py                                                                                          |        0 |        0 |    100% |           |
| django/otto/asgi.py                                                                                           |        8 |        8 |      0% |     10-24 |
| django/otto/celery.py                                                                                         |       16 |        1 |     94% |        69 |
| django/otto/context\_processors.py                                                                            |       10 |        4 |     60% |      9-13 |
| django/otto/forms.py                                                                                          |       68 |        4 |     94% |72, 74, 202-203 |
| django/otto/management/commands/delete\_empty\_chats.py                                                       |       19 |        1 |     95% |        29 |
| django/otto/management/commands/delete\_old\_chats.py                                                         |       20 |        2 |     90% |    31, 35 |
| django/otto/management/commands/delete\_text\_extractor\_files.py                                             |       18 |        0 |    100% |           |
| django/otto/management/commands/reset\_app\_data.py                                                           |      124 |       20 |     84% |67-72, 90, 104-109, 129-134, 155-160, 174-175, 180-183, 198-203, 214 |
| django/otto/management/commands/update\_exchange\_rate.py                                                     |       22 |        2 |     91% |     35-37 |
| django/otto/metrics/\_\_init\_\_.py                                                                           |        0 |        0 |    100% |           |
| django/otto/metrics/activity\_metrics.py                                                                      |        2 |        0 |    100% |           |
| django/otto/metrics/feedback\_metrics.py                                                                      |        3 |        3 |      0% |       1-8 |
| django/otto/migrations/0001\_initial.py                                                                       |        8 |        0 |    100% |           |
| django/otto/migrations/0002\_costtype\_feature\_short\_name\_cost.py                                          |        6 |        0 |    100% |           |
| django/otto/migrations/0003\_cost\_document\_cost\_message\_cost\_request\_id\_and\_more.py                   |        5 |        0 |    100% |           |
| django/otto/migrations/0004\_pilot\_user\_pilot.py                                                            |        5 |        0 |    100% |           |
| django/otto/migrations/0005\_alter\_cost\_date\_incurred.py                                                   |        4 |        0 |    100% |           |
| django/otto/migrations/0006\_cost\_law.py                                                                     |        5 |        0 |    100% |           |
| django/otto/migrations/0007\_alter\_cost\_feature.py                                                          |        4 |        0 |    100% |           |
| django/otto/migrations/0008\_alter\_cost\_cost\_type\_alter\_cost\_document\_and\_more.py                     |        6 |        0 |    100% |           |
| django/otto/migrations/0008\_user\_default\_preset.py                                                         |        5 |        0 |    100% |           |
| django/otto/migrations/0009\_alter\_cost\_cost\_type.py                                                       |        5 |        0 |    100% |           |
| django/otto/migrations/0009\_merge\_20241011\_1541.py                                                         |        4 |        0 |    100% |           |
| django/otto/migrations/0010\_alter\_cost\_cost\_type.py                                                       |        5 |        0 |    100% |           |
| django/otto/migrations/0010\_user\_daily\_max.py                                                              |        4 |        0 |    100% |           |
| django/otto/migrations/0011\_merge\_20241015\_2016.py                                                         |        4 |        0 |    100% |           |
| django/otto/migrations/0011\_remove\_user\_daily\_max\_user\_weekly\_max\_and\_more.py                        |        4 |        0 |    100% |           |
| django/otto/migrations/0012\_remove\_user\_weekly\_max\_override\_user\_weekly\_bonus.py                      |        4 |        0 |    100% |           |
| django/otto/migrations/0013\_alter\_feedback\_otto\_version.py                                                |        4 |        0 |    100% |           |
| django/otto/migrations/0013\_merge\_20241018\_2115.py                                                         |        4 |        0 |    100% |           |
| django/otto/migrations/0014\_merge\_20241104\_1813.py                                                         |        4 |        0 |    100% |           |
| django/otto/migrations/0015\_alter\_feedback\_feedback\_type.py                                               |        4 |        0 |    100% |           |
| django/otto/migrations/0015\_ottostatus.py                                                                    |        4 |        0 |    100% |           |
| django/otto/migrations/0016\_feedback\_priority\_feedback\_status.py                                          |        4 |        0 |    100% |           |
| django/otto/migrations/0016\_ottostatus\_exchange\_rate.py                                                    |        4 |        0 |    100% |           |
| django/otto/migrations/0017\_alter\_cost\_feature\_alter\_feature\_category.py                                |        4 |        0 |    100% |           |
| django/otto/migrations/0017\_feedback\_admin\_notes\_feedback\_created\_by\_and\_more.py                      |        6 |        0 |    100% |           |
| django/otto/migrations/0018\_feedback\_url\_context.py                                                        |        4 |        0 |    100% |           |
| django/otto/migrations/0019\_alter\_feedback\_feedback\_type.py                                               |        4 |        0 |    100% |           |
| django/otto/migrations/0020\_merge\_20241122\_2011.py                                                         |        4 |        0 |    100% |           |
| django/otto/migrations/0021\_merge\_20241127\_1803.py                                                         |        4 |        0 |    100% |           |
| django/otto/migrations/0022\_rename\_weekly\_bonus\_user\_monthly\_bonus\_and\_more.py                        |        4 |        0 |    100% |           |
| django/otto/migrations/\_\_init\_\_.py                                                                        |        0 |        0 |    100% |           |
| django/otto/models.py                                                                                         |      279 |       30 |     89% |26-28, 76-79, 112, 116-119, 154, 193, 196, 212, 233, 240, 258, 381, 384, 436, 442, 466, 470, 474, 478, 524-525, 539, 543, 547 |
| django/otto/rules.py                                                                                          |      138 |       12 |     91% |26, 42, 49, 51, 120, 157, 186-188, 220, 224-225 |
| django/otto/secure\_models.py                                                                                 |      248 |       91 |     63% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                                                                       |      158 |       23 |     85% |38-41, 51-52, 218-227, 297, 310, 367-374, 403, 493-494 |
| django/otto/tasks.py                                                                                          |       37 |        7 |     81% |11, 16, 38, 48, 61-63 |
| django/otto/templatetags/\_\_init\_\_.py                                                                      |        0 |        0 |    100% |           |
| django/otto/templatetags/filters.py                                                                           |       10 |        1 |     90% |         8 |
| django/otto/templatetags/tags.py                                                                              |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                                                                    |       17 |        0 |    100% |           |
| django/otto/urls.py                                                                                           |       13 |        2 |     85% |    94, 99 |
| django/otto/utils/auth.py                                                                                     |       36 |        6 |     83% |     18-32 |
| django/otto/utils/common.py                                                                                   |       30 |        0 |    100% |           |
| django/otto/utils/decorators.py                                                                               |       60 |        4 |     93% |24-25, 65, 87 |
| django/otto/utils/logging.py                                                                                  |       15 |        0 |    100% |           |
| django/otto/utils/middleware.py                                                                               |       17 |        1 |     94% |        23 |
| django/otto/views.py                                                                                          |      536 |      111 |     79% |58, 63, 68-82, 123, 133-144, 159, 280, 381, 433-436, 452-453, 477, 487-490, 519-529, 541-546, 549, 558, 560-563, 565-566, 568-571, 580, 594, 602, 611, 760, 762, 764, 778, 780, 791-794, 804-810, 820, 822, 824, 829-849, 869-877, 886-906, 994, 1025, 1058-1081 |
| django/otto/wsgi.py                                                                                           |        4 |        4 |      0% |     10-16 |
| django/postgres\_wrapper/\_\_init\_\_.py                                                                      |        0 |        0 |    100% |           |
| django/postgres\_wrapper/base.py                                                                              |        6 |        0 |    100% |           |
| django/tests/\_\_init\_\_.py                                                                                  |        0 |        0 |    100% |           |
| django/tests/chat/test\_answer\_sources.py                                                                    |       38 |        0 |    100% |           |
| django/tests/chat/test\_chat\_models.py                                                                       |       36 |        1 |     97% |        48 |
| django/tests/chat/test\_chat\_options.py                                                                      |       54 |        0 |    100% |           |
| django/tests/chat/test\_chat\_procs.py                                                                        |      169 |        0 |    100% |           |
| django/tests/chat/test\_chat\_readonly.py                                                                     |       33 |        0 |    100% |           |
| django/tests/chat/test\_chat\_translate.py                                                                    |       37 |        0 |    100% |           |
| django/tests/chat/test\_chat\_views.py                                                                        |      594 |       11 |     98% |   579-597 |
| django/tests/conftest.py                                                                                      |      169 |        5 |     97% |36, 209, 239-243 |
| django/tests/laws/conftest.py                                                                                 |        9 |        0 |    100% |           |
| django/tests/laws/test\_laws\_utils.py                                                                        |       45 |        0 |    100% |           |
| django/tests/laws/test\_laws\_views.py                                                                        |       45 |        0 |    100% |           |
| django/tests/librarian/test\_document\_loading.py                                                             |      167 |        0 |    100% |           |
| django/tests/librarian/test\_librarian.py                                                                     |      227 |        0 |    100% |           |
| django/tests/librarian/test\_markdown\_splitter.py                                                            |      282 |        0 |    100% |           |
| django/tests/otto/test\_budget.py                                                                             |       37 |        0 |    100% |           |
| django/tests/otto/test\_cleanup.py                                                                            |      211 |        0 |    100% |           |
| django/tests/otto/test\_exchange\_rate\_update.py                                                             |       11 |        0 |    100% |           |
| django/tests/otto/test\_feedback\_dashboard.py                                                                |      109 |        0 |    100% |           |
| django/tests/otto/test\_load\_test.py                                                                         |       64 |        0 |    100% |           |
| django/tests/otto/test\_manage\_users.py                                                                      |      129 |        0 |    100% |           |
| django/tests/otto/test\_otto\_forms.py                                                                        |       11 |        0 |    100% |           |
| django/tests/otto/test\_otto\_models.py                                                                       |       47 |        0 |    100% |           |
| django/tests/otto/test\_otto\_views.py                                                                        |       63 |        0 |    100% |           |
| django/tests/otto/test\_utils\_common.py                                                                      |       13 |        0 |    100% |           |
| django/tests/otto/test\_utils\_middleware.py                                                                  |       35 |        0 |    100% |           |
| django/tests/settings.py                                                                                      |        0 |        0 |    100% |           |
| django/tests/template\_wizard/test\_template\_wizard\_views.py                                                |       19 |        0 |    100% |           |
| django/tests/text\_extractor/test\_tasks.py                                                                   |       39 |        0 |    100% |           |
| django/tests/text\_extractor/test\_utils.py                                                                   |      106 |        0 |    100% |           |
| django/tests/text\_extractor/test\_views.py                                                                   |       95 |        2 |     98% |  150, 161 |
| django/text\_extractor/\_\_init\_\_.py                                                                        |        0 |        0 |    100% |           |
| django/text\_extractor/admin.py                                                                               |        1 |        1 |      0% |         1 |
| django/text\_extractor/apps.py                                                                                |       11 |        1 |     91% |        21 |
| django/text\_extractor/migrations/0001\_initial.py                                                            |        6 |        0 |    100% |           |
| django/text\_extractor/migrations/0002\_remove\_outputfile\_file\_id\_alter\_outputfile\_file\_name.py        |        4 |        0 |    100% |           |
| django/text\_extractor/migrations/0003\_remove\_outputfile\_file\_outputfile\_celery\_task\_ids\_and\_more.py |        4 |        0 |    100% |           |
| django/text\_extractor/migrations/0004\_outputfile\_usd\_cost.py                                              |        4 |        0 |    100% |           |
| django/text\_extractor/migrations/\_\_init\_\_.py                                                             |        0 |        0 |    100% |           |
| django/text\_extractor/models.py                                                                              |       17 |        1 |     94% |        28 |
| django/text\_extractor/tasks.py                                                                               |       18 |        2 |     89% |     32-33 |
| django/text\_extractor/urls.py                                                                                |        4 |        0 |    100% |           |
| django/text\_extractor/utils.py                                                                               |      208 |       40 |     81% |57-80, 121-122, 174, 211, 285-287, 341-345, 352-353, 359, 365-369 |
| django/text\_extractor/views.py                                                                               |      108 |       21 |     81% |41, 59-74, 84, 98-106, 119-125, 142, 146, 163, 173, 193-194 |
|                                                                                                     **TOTAL** | **9565** | **1160** | **88%** |           |


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